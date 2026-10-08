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

    async def parse_ocr_screenshot(self, attachment):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status != 200:
                        return None
                    image_bytes = await resp.read()

            img = Image.open(io.BytesIO(image_bytes))
            width, height = img.size
            
            # Crop to the right 35% (Quest Panel)
            crop_box = (int(width * 0.65), 0, width, height)
            img_cropped = img.crop(crop_box)
            
            img_gray = img_cropped.convert('L')
            img_enhanced = ImageOps.autocontrast(img_gray)
            
            output_buffer = io.BytesIO()
            img_enhanced.save(output_buffer, format='PNG')
            output_buffer.seek(0)

            payload = {
                'language': 'eng',
                'isOverlayRequired': False,
                'isTable': True,
                'OCREngine': 2
            }
            headers = {'apikey': config.OCR_API_KEY}
            
            form = aiohttp.FormData()
            form.add_field('file', output_buffer, filename='cropped.png', content_type='image/png')
            for key, value in payload.items():
                form.add_field(key, str(value))

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.post('https://api.ocr.space/parse/image', data=form, headers=headers) as resp:
                    data = await resp.json()
                    
                    if data.get("IsErroredOnProcessing"):
                        return None
                    
                    results = data.get("ParsedResults") or [{}]
                    text = results[0].get("ParsedText", "")
                    
                    ofa_match = re.search(r'(?:OFA|One for All).*?(\d+)\s*/\s*(\d+)', text, re.IGNORECASE | re.DOTALL)
                    dyp_match = re.search(r'(?:DYP|Do Your Part).*?(\d+)\s*/\s*(\d+)', text, re.IGNORECASE | re.DOTALL)
                    je_match = re.search(r'(?:JE|Joint Effort).*?(\d+\.?\d*\s*[MB]?)\s*/\s*(\d+\.?\d*\s*[MB]?)', text, re.IGNORECASE | re.DOTALL)
                    
                    return {
                        "ofa_target": ofa_match.group(2) if ofa_match else None,
                        "dyp_target": int(dyp_match.group(2)) if dyp_match else None,
                        "je_target": je_match.group(2) if je_match else None
                    }
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            print(f"[OCR Error] {e}")
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

        if action is None:
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
            
            dyp_status = f"{state['dyp_progress']}/{state['dyp_target']} contributions"
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
            
            if ocr_data is None:
                await ctx.send("⚠️ OCR failed or timed out. Using default targets (DYP: 10, JE: 4). Use `!race set dyp <num>` or `!race set je <num>` to correct.")
                ocr_data = {"ofa_target": "Unknown", "dyp_target": 10, "je_target": "4"}
            elif ocr_data['dyp_target'] is None or ocr_data['je_target'] is None:
                await ctx.send("⚠️ Could not detect all targets from screenshot. Using defaults where missing.")
                ocr_data['ofa_target'] = ocr_data['ofa_target'] or "Unknown"
                ocr_data['dyp_target'] = ocr_data['dyp_target'] or 10
                ocr_data['je_target'] = ocr_data['je_target'] or "4"

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
                await ctx.send("Usage: `!race set dyp 12` or `!race set je 49.4 M`")
                return
            parts = args.split()
            if len(parts) >= 2 and parts[0].lower() in ("dyp", "je"):
                if parts[0].lower() == "dyp":
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
                await ctx.send("Usage: `!race set dyp 12` or `!race set je 49.4 M`")

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
            
            role_pings = " ".join([f"<@&{rid}>" for rid in config.OFA_PING_ROLES])
            msg = await ctx.send(f"🏆 {ctx.author.mention} is claiming **OFA**! {role_pings}\nPlease react ✅ to approve or ❌ to deny.")
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            
            db.add_pending_ofa(msg.id, ctx.channel.id, ctx.author.id)

        elif action == "dyp":
            # NEW: Staff-only DYP skip command
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

            if args and args.lower() in ("done", "+1"):
                success = db.increment_dyp(ctx.channel.id, ctx.author.id)
                if success:
                    state = db.get_state(ctx.channel.id)
                    await ctx.send(f"✅ DYP contribution logged for {ctx.author.mention}. Progress: `{state['dyp_progress']}/{state['dyp_target']}`")
                    if state['dyp_progress'] == state['dyp_target']:
                        await ctx.send("🎉 **DYP Target Reached!**")
                else:
                    await ctx.send("❌ You have already contributed to DYP for this node, or the target is already reached.")
            else:
                await ctx.send("Usage: `!race dyp done`, `!race dyp +1`, or `!race dyp skip` (staff)")

        elif action == "je":
            if args and args.lower() in ("done", "skip"):
                if not is_admin:
                    await ctx.send("❌ Staff only for JE done/skip.")
                    return
                db.update_state(ctx.channel.id, je_done=1)
                word = "completed" if args.lower() == "done" else "skipped"
                await ctx.send(f"⏭️ **Joint Effort (JE) {word}!** Waiting for `!race newnode` screenshot.")
            elif args and args.lower() == "+1":
                if state['je_done']:
                    await ctx.send("❌ JE is already completed for this node.")
                    return
                try:
                    curr = float(re.sub(r'[^\d.]', '', str(state['je_progress'])))
                    target = float(re.sub(r'[^\d.]', '', str(state['je_target'])))
                    curr = min(curr + 1, target)
                    state['je_progress'] = str(int(curr)) if curr.is_integer() else str(curr)
                except ValueError:
                    state['je_progress'] = str(state['je_target'])
                
                db.update_state(ctx.channel.id, je_progress=state['je_progress'])
                await ctx.send(f"✅ JE contribution logged. Progress: `{state['je_progress']}/{state['je_target']}`")
                
                if str(state['je_progress']) == str(state['je_target']) or float(re.sub(r'[^\d.]', '', state['je_progress'])) >= float(re.sub(r'[^\d.]', '', state['je_target'])):
                    db.update_state(ctx.channel.id, je_done=1)
                    await ctx.send("🎉 **JE Target Reached! Node complete.**")
            else:
                await ctx.send("Usage: `!race je +1`, `!race je done`, or `!race je skip`")
        
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
                        curr_je = float(re.sub(r'[^\d.]', '', str(state['je_progress'])))
                        tgt_je = float(re.sub(r'[^\d.]', '', str(state['je_target'])))
                        if curr_je < tgt_je:
                            lines.append(f"⏰ **JE:** `{state['je_progress']}/{state['je_target']}`")
                    except ValueError:
                        lines.append(f"⏰ **JE:** `{state['je_progress']}/{state['je_target']}`")
                
                if state['ofa_holder'] and not state['ofa_done']:
                    lines.append(f"⏰ **OFA:** In progress by <@{state['ofa_holder']}>")
                elif state['ofa_holder'] and state['ofa_done']:
                    pass
                elif pending_ofa is not None:
                    lines.append(f"⏳ **OFA:** Pending approval for <@{pending_ofa['user_id']}>")
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