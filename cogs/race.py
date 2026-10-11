import discord
from discord.ext import commands, tasks
import aiohttp
import re
import time
import asyncio
import io
import config
from PIL import Image, ImageOps
from utils.database import db

# --- Helper Functions for K/M/B parsing ---
UNITS = {'K': 1e3, 'M': 1e6, 'B': 1e9}

def parse_amount(s):
    m = re.fullmatch(r'\s*([\d.,]+)\s*([KMB])?\s*', str(s), re.IGNORECASE)
    if not m:
        raise ValueError(f"bad amount: {s}")
    num = float(m.group(1).replace(',', '.'))
    return num * UNITS.get((m.group(2) or '').upper(), 1)

def format_amount(v):
    for suffix, div in (('B', 1e9), ('M', 1e6), ('K', 1e3)):
        if v >= div:
            return f"{round(v / div, 2):g} {suffix}"
    return f"{round(v, 2):g}"


def is_race_channel():
    async def predicate(ctx):
        return ctx.channel.id in config.RACE_CHANNELS
    return commands.check(predicate)


class RaceManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()

    async def _run_ocr(self, jpg_bytes, engine):
        form = aiohttp.FormData()
        form.add_field('file', jpg_bytes, filename='q.jpg', content_type='image/jpeg')
        form.add_field('language', 'eng')
        form.add_field('isOverlayRequired', 'true')
        form.add_field('scale', 'true')
        form.add_field('OCREngine', str(engine))
        headers = {'apikey': config.OCR_API_KEY}

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                async with s.post('https://api.ocr.space/parse/image',
                                  data=form, headers=headers) as resp:
                    data = await resp.json(content_type=None)
        except Exception as e:
            print(f"[OCR Error] engine {engine}: {e}", flush=True)
            return None

        if data.get("IsErroredOnProcessing"):
            print(f"[OCR Error] API: {data.get('ErrorMessage')}", flush=True)
            return None

        res = (data.get("ParsedResults") or [{}])[0]
        text = res.get("ParsedText", "")
        lines = (res.get("TextOverlay") or {}).get("Lines") or []
        print(f"[OCR Raw] engine {engine}: {text!r}", flush=True)
        if not text:
            print(f"[OCR] empty result, exit code: {res.get('FileParseExitCode')}, "
                  f"msg: {res.get('ErrorMessage')}", flush=True)

        return self._parse_cards(text)

    def _parse_cards(self, text):
        FRAC = re.compile(
            r'(\d+(?:[.,]\d+)?(?:\s*[KMB]\b)?)\s*[/|\\]\s*(\d+(?:[.,]\d+)?(?:\s*[KMB]\b)?)',
            re.IGNORECASE
        )

        def norm(t):
            t = t.strip().replace(',', '.')
            return re.sub(r'(\d)\s*([KMB])$', r'\1 \2', t, flags=re.IGNORECASE).upper()

        # Split the text into the three cards by their headers
        def section(start, end):
            s = re.search(start, text, re.IGNORECASE)
            if not s:
                return ""
            rest = text[s.end():]
            if end:
                e = re.search(end, rest, re.IGNORECASE)
                if e:
                    return rest[:e.start()]
            return rest

        ofa_txt = section(r'One\s*for\s*All', r'Do\s*Your\s*Part')
        dyp_txt = section(r'Do\s*Your\s*Part', r'Joint\s*Effort')
        je_txt = section(r'Joint\s*Effort', r'COMPLETE')

        def target_from(sec, kind):
            # 1) Prefer an explicit a/b fraction
            m = FRAC.search(sec)
            if m:
                return norm(m.group(2))
            # 2) Fall back to the number in the description text
            if kind == "ofa":
                m = re.search(r'(?:craft|collect|hatch|breed|feed|rank up|level up|win|defeat)\s+(\d+(?:[.,]\d+)?\s*[KMB]?)', sec, re.IGNORECASE)
            elif kind == "dyp":
                m = re.search(r'(\d+)\s+members', sec, re.IGNORECASE)
            else:  # je
                m = re.search(r'(?:craft|collect|hatch|breed|feed|rank up|level up|win|defeat|with|up)\s+(\d+(?:[.,]\d+)?\s*[KMB]?)', sec, re.IGNORECASE)
            return norm(m.group(1)) if m else None

        ofa = target_from(ofa_txt, "ofa")
        dyp = target_from(dyp_txt, "dyp")
        je = target_from(je_txt, "je")
        print(f"[OCR] parsed -> OFA={ofa} DYP={dyp} JE={je}", flush=True)

        if ofa and dyp and je:
            return [ofa, dyp, je]
        return None

    async def parse_ocr_screenshot(self, attachment):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status != 200:
                        return None
                    image_bytes = await resp.read()

            img = Image.open(io.BytesIO(image_bytes))
            width, height = img.size

            if width / height > 1.3:
                img = img.crop((int(width * 0.64), int(height * 0.22),
                                width, int(height * 0.93)))

            img = img.resize((int(img.width * 1.5), int(img.height * 1.5)), Image.LANCZOS)
            buf = io.BytesIO()
            img.convert('RGB').save(buf, format='JPEG', quality=90)   # keep colour
            jpg_bytes = buf.getvalue()
            print(f"[OCR] upload size: {len(jpg_bytes) / 1024:.0f} KB", flush=True)

            for engine in (2, 1):
                targets = await self._run_ocr(jpg_bytes, engine)
                print(f"[OCR] engine {engine} targets: {targets}", flush=True)
                if targets and len(targets) == 3:
                    ofa, dyp, je = targets
                    try:
                        dyp = int(dyp)
                    except ValueError:
                        continue
                    return {"ofa_target": ofa, "dyp_target": dyp, "je_target": je}
            return None

        except asyncio.TimeoutError:
            print("[OCR Error] Timeout", flush=True)
            return None
        except Exception as e:
            print(f"[OCR Error] {e}", flush=True)
            return None

    @commands.command(name="race")
    @is_race_channel()
    async def race(self, ctx, action: str = None, *, args: str = None):
        state = db.get_state(ctx.channel.id)
        is_admin = any(role.id in config.ADMIN_ROLES for role in ctx.author.roles)

        if action is not None:
            action = action.lower()
            if not state['event_active'] and not is_admin:
                await ctx.send("⏸️ The race event is currently disabled by staff.")
                return

        if action is None or action == "status":
            embed = discord.Embed(title="🏁 Current Race Node Status", color=discord.Color.blue())
            embed.add_field(name="Node", value=f"**Lap {state['lap']}.{state['node']}**", inline=False)
            
            if state['ofa_holder']:
                if state['ofa_done']:
                    ofa_status = f"✅ Completed by <@{state['ofa_holder']}> (Target: {state['ofa_target']})"
                else:
                    ofa_status = f"⏳ In Progress by <@{state['ofa_holder']}> (Target: {state['ofa_target']})"
            else:
                ofa_status = "Available ✅"
            embed.add_field(name="One For All (OFA)", value=ofa_status, inline=False)
            
            dyp_status = "✅ Completed" if state['dyp_progress'] >= state['dyp_target'] else f"{state['dyp_progress']}/{state['dyp_target']} contributions"
            embed.add_field(name="Do Your Part (DYP)", value=dyp_status, inline=True)
            
            je_status = "✅ Completed" if state['je_done'] else f"{state['je_progress']}/{state['je_target']} contributions"
            embed.add_field(name="Joint Effort (JE)", value=je_status, inline=True)
            
            await ctx.send(embed=embed)
            return

        if action == "newnode":
            if not is_admin:
                await ctx.send("❌ Staff only.")
                return
                
            if not ctx.message.attachments:
                await ctx.send("❌ Please attach a screenshot of the new race node.")
                return
            
            await ctx.send("🔍 Analyzing screenshot (with enhanced OCR)...")
            ocr_data = await self.parse_ocr_screenshot(ctx.message.attachments[0])
            
            if ocr_data is None or ocr_data['dyp_target'] is None or ocr_data['je_target'] is None or ocr_data['ofa_target'] is None:
                await ctx.send("⚠️ OCR failed or timed out. Using default targets (OFA: Unknown, DYP: 10, JE: 4). Use `!race set ofa <num>`, `!race set dyp <num>`, or `!race set je <num>` to correct.")
                ocr_data = {"ofa_target": "Unknown", "dyp_target": 10, "je_target": "4"}

            new_node = state['node'] + 1
            new_lap = state['lap']
            if new_node > config.NODES_PER_LAP:
                new_node = 1
                new_lap += 1
                
            db.clear_pending_ofa_for_channel(ctx.channel.id)
            db.reset_dyp_contributors(ctx.channel.id)
                
            db.update_state(
                ctx.channel.id,
                lap=new_lap, node=new_node,
                ofa_holder=None, ofa_target=ocr_data['ofa_target'], ofa_done=0,
                dyp_progress=0, dyp_target=ocr_data['dyp_target'],
                je_progress="0", je_target=ocr_data['je_target'], je_done=0
            )
            await ctx.send(f"✅ Node initialized: **Lap {new_lap}.{new_node}**\n"
                           f"OFA: `{ocr_data['ofa_target']}` | DYP: `{ocr_data['dyp_target']}` | JE: `{ocr_data['je_target']}`")

        elif action == "set":
            if not is_admin:
                await ctx.send("❌ Staff only.")
                return
            if not args:
                await ctx.send("Usage: `!race set ofa 4`, `!race set dyp 12` or `!race set je 5.29 M`")
                return
            parts = args.split()
            if len(parts) >= 2 and parts[0].lower() in ("dyp", "je", "ofa"):
                if parts[0].lower() == "ofa":
                    target = " ".join(parts[1:])
                    db.update_state(ctx.channel.id, ofa_target=target)
                    await ctx.send(f"✅ OFA target set to {target}.")
                elif parts[0].lower() == "dyp":
                    try:
                        target = int(parts[1])
                        if target < 1: raise ValueError
                    except ValueError:
                        await ctx.send("❌ DYP target must be a positive integer.")
                        return
                    db.update_state(ctx.channel.id, dyp_target=target)
                    await ctx.send(f"✅ DYP target set to {target}.")
                else:
                    target = " ".join(parts[1:])
                    db.update_state(ctx.channel.id, je_target=target)
                    await ctx.send(f"✅ JE target set to {target}.")
            else:
                await ctx.send("Usage: `!race set ofa 4`, `!race set dyp 12` or `!race set je 5.29 M`")

        elif action == "ofa":
            if args and args.lower() == "done":
                if not state['ofa_holder']:
                    await ctx.send("❌ No one has claimed OFA for this node yet.")
                    return
                if ctx.author.id != state['ofa_holder'] and not is_admin:
                    await ctx.send(f"❌ Only <@{state['ofa_holder']}> or staff can mark OFA as done.")
                    return
                if state['ofa_done']:
                    await ctx.send("✅ OFA is already marked as completed for this node.")
                    return
                db.update_state(ctx.channel.id, ofa_done=1)
                await ctx.send(f"🎉 **OFA marked as COMPLETED** by <@{state['ofa_holder']}>!")
                return

            if state['ofa_holder']:
                await ctx.send(f"🔒 OFA is already claimed by <@{state['ofa_holder']}>.")
                return
            
            pending = db.get_pending_ofa_by_channel(ctx.channel.id)
            if pending:
                await ctx.send(f"⏳ An OFA claim for <@{pending['user_id']}> is already pending approval.")
                return
            
            ping_roles = config.OFA_PING_ROLES.get(ctx.channel.id, [])
            role_pings = " ".join([f"<@&{rid}>" for rid in ping_roles])
            
            msg = await ctx.send(f"🏆 {ctx.author.mention} is claiming **OFA**! {role_pings}\nPlease react ✅ to approve or ❌ to deny.")
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            
            db.add_pending_ofa(msg.id, ctx.channel.id, ctx.author.id)

        elif action == "dyp":
            if args and args.lower() == "skip":
                if not is_admin:
                    await ctx.send("❌ Staff only for DYP skip.")
                    return
                if state['dyp_progress'] >= state['dyp_target']:
                    await ctx.send("❌ DYP target already reached.")
                    return
                db.update_state(ctx.channel.id, dyp_progress=state['dyp_target'])
                await ctx.send(f"⏭️ **DYP skipped by staff!** Target reached ({state['dyp_target']}/{state['dyp_target']}).")
                return

            if args and (args.lower() == "done" or args.lower().startswith("+")):
                amount = 1
                if args.lower().startswith("+"):
                    try:
                        amount = int(args[1:])
                    except ValueError:
                        await ctx.send("❌ Invalid amount. Example: `!race dyp +5`")
                        return
                
                new_progress = min(state['dyp_progress'] + amount, state['dyp_target'])
                db.update_state(ctx.channel.id, dyp_progress=new_progress)
                await ctx.send(f"✅ DYP contribution logged for {ctx.author.mention}. Progress: `{new_progress}/{state['dyp_target']}`")
                if new_progress >= state['dyp_target']:
                    await ctx.send("🎉 **DYP Target Reached!**")
            else:
                await ctx.send("Usage: `!race dyp done`, `!race dyp +1`, or `!race dyp +5`")

        elif action == "je":
            if args and args.lower() == "skip":
                if not is_admin:
                    await ctx.send("❌ Staff only for JE skip.")
                    return
                db.update_state(ctx.channel.id, je_done=1)
                await ctx.send("⏭️ **Joint Effort (JE) skipped!** Waiting for `!race newnode` screenshot.")

            elif args and args.lower() == "done":
                if state['je_done']:
                    await ctx.send("✅ JE is already completed for this node.")
                    return
                db.update_state(ctx.channel.id, je_done=1, je_progress=str(state['je_target']))
                await ctx.send(f"🎉 **Joint Effort (JE) completed** by {ctx.author.mention}! Waiting for `!race newnode` screenshot.")

            elif args and args.startswith("+"):
                if state['je_done']:
                    await ctx.send("❌ JE is already completed for this node.")
                    return
                try:
                    amount = parse_amount(args[1:])
                    curr = parse_amount(state['je_progress'])
                    target = parse_amount(state['je_target'])
                except ValueError:
                    await ctx.send("❌ Invalid amount. Examples: `!race je +4` or `!race je +500k`")
                    return

                new_val = min(curr + amount, target)
                new_progress = format_amount(new_val)
                db.update_state(ctx.channel.id, je_progress=new_progress)
                await ctx.send(f"✅ JE contribution logged. Progress: `{new_progress}/{state['je_target']}`")

                if new_val >= target:
                    db.update_state(ctx.channel.id, je_done=1)
                    await ctx.send("🎉 **JE Target Reached! Node complete.**")
            else:
                await ctx.send("Usage: `!race je +4`, `!race je +500k`, `!race je done` or `!race je skip` (staff)")
        
        else:
            await ctx.send("❌ Unknown option. See `?help` for available commands.")

    @commands.command(name="dmp")
    @is_race_channel()
    async def dmp(self, ctx):
        state = db.get_state(ctx.channel.id)
        is_admin = any(role.id in config.ADMIN_ROLES for role in ctx.author.roles)
        if not state['event_active'] and not is_admin:
            await ctx.send("⏸️ The race event is currently disabled by staff.")
            return
            
        success = db.increment_dyp(ctx.channel.id, ctx.author.id)
        if success:
            state = db.get_state(ctx.channel.id)
            await ctx.send(f"✅ DYP contribution logged for {ctx.author.mention}. Progress: `{state['dyp_progress']}/{state['dyp_target']}`")
            if state['dyp_progress'] == state['dyp_target']:
                await ctx.send("🎉 **DYP Target Reached!**")
        else:
            await ctx.send("❌ You have already contributed to DYP for this node, or the target is already reached.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id:
            return
            
        pending = db.get_pending_ofa(payload.message_id)
        if not pending:
            return
            
        channel_id, user_id = pending['channel_id'], pending['user_id']
        if payload.channel_id != channel_id:
            return

        member = payload.member
        if member is None:
            return
            
        is_admin = any(role.id in config.ADMIN_ROLES for role in member.roles)
        if not is_admin:
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return

        if str(payload.emoji) == "✅":
            if db.get_state(channel_id)['ofa_holder']:
                db.delete_pending_ofa(payload.message_id)
                return
                
            db.update_state(channel_id, ofa_holder=user_id, ofa_done=0)
            db.clear_pending_ofa_for_channel(channel_id)
            await channel.send(f"✅ OFA officially locked for <@{user_id}>!")
            db.delete_pending_ofa(payload.message_id)
        elif str(payload.emoji) == "❌":
            await channel.send(f"❌ OFA claim for <@{user_id}> was denied. Others may now claim it.")
            db.delete_pending_ofa(payload.message_id)

    @commands.command(name="ignore")
    @is_race_channel()
    async def ignore(self, ctx, time_str: str):
        match = re.fullmatch(r"(\d+)([mh])", time_str.lower())
        if not match:
            await ctx.send("❌ Invalid format. Use `!ignore 1h` or `!ignore 30m`. Use `!unignore` to reset.")
            return
        
        amount, unit = int(match.group(1)), match.group(2)
        seconds = amount * 60 if unit == 'm' else amount * 3600
        mute_until = time.time() + seconds
        
        db.update_state(ctx.channel.id, mute_until=mute_until)
        await ctx.send(f"🔇 Race notifications muted for {amount}{unit}.")

    @commands.command(name="unignore")
    @is_race_channel()
    async def unignore(self, ctx):
        db.update_state(ctx.channel.id, mute_until=0)
        await ctx.send("🔊 Race notifications unmuted.")

    @tasks.loop(minutes=15)
    async def reminder_loop(self):
        current_time = time.time()
        for channel_id in config.RACE_CHANNELS:
            state = db.get_state(channel_id)
            
            if not state['event_active'] or state['mute_until'] > current_time:
                continue
                
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
                
            try:
                lines = []
                pending_ofa = db.get_pending_ofa_by_channel(channel_id)

                if state['dyp_target'] > 0 and state['dyp_progress'] < state['dyp_target']:
                    lines.append(f"⏰ **DYP:** `{state['dyp_progress']}/{state['dyp_target']}`")
                
                je_done = state['je_done']
                if not je_done:
                    try:
                        if parse_amount(state['je_progress']) < parse_amount(state['je_target']):
                            lines.append(f"⏰ **JE:** `{state['je_progress']}/{state['je_target']}`")
                    except ValueError:
                        lines.append(f"⏰ **JE:** `{state['je_progress']}/{state['je_target']}`")
                
                if state['ofa_holder'] and not state['ofa_done']:
                    ofa_mention = f"<@{state['ofa_holder']}>"
                    lines.append(f"⏰ **OFA:** In progress by {ofa_mention}")
                elif state['ofa_holder'] and state['ofa_done']:
                    pass
                elif pending_ofa is not None:
                    pending_mention = f"<@{pending_ofa['user_id']}>"
                    lines.append(f"⏳ **OFA:** Pending approval for {pending_mention}")
                else:
                    lines.append("⏰ **OFA:** Still available! Claim with `!race OFA`")
                    
                if lines:
                    combined_message = "**Race Reminder**\n" + "\n".join(lines)
                    await channel.send(combined_message)
                    
            except discord.Forbidden:
                print(f"[reminder] 403 Forbidden: No access to channel {channel_id}. Check channel permissions.")
            except discord.HTTPException as e:
                print(f"[reminder] HTTP error for {channel_id}: {e!r}")

    @reminder_loop.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(RaceManager(bot))
