import discord
from discord.ext import commands, tasks
import re
import time
import datetime
import config
from utils.database import db
from utils.ocr_engine import process_race_image

UNITS = {'K': 1e3, 'M': 1e6, 'B': 1e9}

def parse_amount(s):
    m = re.fullmatch(r'\s*([\d.,]+)\s*([KMB])?\s*', str(s), re.IGNORECASE)
    if not m: raise ValueError(f"bad amount: {s}")
    return float(m.group(1).replace(',', '.')) * UNITS.get((m.group(2) or '').upper(), 1)

def format_amount(v):
    for suffix, div in (('B', 1e9), ('M', 1e6), ('K', 1e3)):
        if v >= div: return f"{round(v / div, 2):g} {suffix}"
    return f"{round(v, 2):g}"

NUM = re.compile(r'(?<![\w.])\d+(?:[.,]\d+)?', re.IGNORECASE)
CHUNK = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:(billion|million|thousand|[KMB])(?![A-Za-z]))?\s*(.*)', re.IGNORECASE | re.DOTALL)
SUFFIX_MAP = {'k': 'K', 'thousand': 'K', 'm': 'M', 'million': 'M', 'b': 'B', 'billion': 'B'}

def parse_node_args(args):
    if '|' in args:
        chunks = [c.strip() for c in args.split('|')]
        if len(chunks) != 3: raise ValueError("need exactly 3 parts")
    else:
        ms = list(NUM.finditer(args))
        if len(ms) < 3: raise ValueError("need 3 numbers")
        starts = [m.start() for m in ms[:3]]
        ends = starts[1:] + [len(args)]
        chunks = [args[s:e].strip() for s, e in zip(starts, ends)]
    out = []
    for c in chunks:
        m = CHUNK.match(c)
        if not m: raise ValueError(f"can't read: {c}")
        suffix = SUFFIX_MAP.get((m.group(2) or '').lower(), '')
        out.append((f"{m.group(1).replace(',', '.')} {suffix}".strip(), m.group(3).strip()))
    return out

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

    async def get_dyp_pending_pings(self, channel, state):
        if state['dyp_progress'] >= state['dyp_target']: return ""
        
        if channel.id == config.LC_CHANNEL: role_id = config.LC_MEMBERS
        elif channel.id == config.FBW_CHANNEL: role_id = config.FBW_MEMBERS
        else: role_id = config.TESTER_ROLE

        role = channel.guild.get_role(role_id)
        if not role: return ""

        contributors = db.get_dyp_contributors(channel.id)
        mentions = []
        
        utc_now = datetime.datetime.now(datetime.timezone.utc)
        utc_hour = utc_now.hour + (utc_now.minute / 60.0)

        for member in role.members:
            if member.bot or member.id in contributors or db.is_user_muted(member.id): continue

            user_offset = None
            for tz_role_id, offset in config.TIMEZONE_ROLES.items():
                if any(r.id == tz_role_id for r in member.roles):
                    user_offset = offset
                    break
            
            if user_offset is not None:
                local_hour = (utc_hour + user_offset) % 24
                if local_hour >= 23.0 or local_hour < 8.0:
                    continue 

            mentions.append(member.mention)
            
        return " ".join(mentions)

    async def send_dyp_ping(self, channel, state):
        if state['dyp_target'] == 0 or state['dyp_progress'] >= state['dyp_target']: return
        mentions = await self.get_dyp_pending_pings(channel, state)
        if not mentions: return
        await channel.send(f"⏰ **DYP Pending:** `{state['dyp_progress']}/{state['dyp_target']}`\nPlease complete pending race tasks!\n{mentions}")

    async def setup_node(self, ctx, state, ofa_t, dyp_t, je_t, desc, dyp_prog=0, je_prog="0"):
        new_node = state['node'] + 1
        new_lap = state['lap']
        if new_node > config.NODES_PER_LAP:
            new_node = 1
            new_lap += 1

        db.clear_pending_ofa_for_channel(ctx.channel.id)
        db.reset_dyp_contributors(ctx.channel.id)

        # Mark existing progress as dummy IDs so the bot doesn't ping for completed slots
        if int(dyp_prog) > 0:
            for i in range(int(dyp_prog)):
                db.cursor.execute("INSERT OR IGNORE INTO dyp_contributors (channel_id, user_id) VALUES (?, ?)", (ctx.channel.id, 900000 + i))
            db.conn.commit()

        db.update_state(
            ctx.channel.id,
            lap=new_lap, node=new_node,
            ofa_holder=None, ofa_target=ofa_t, ofa_done=0,
            dyp_progress=int(dyp_prog), dyp_target=int(dyp_t),
            je_progress=str(je_prog), je_target=je_t, je_done=0,
            task_desc=desc
        )
        
        await ctx.send(f"✅ Node initialized: **Lap {new_lap}.{new_node}**\n"
                       f"**OFA:** `{ofa_t}`\n**DYP:** `{dyp_prog}/{dyp_t}`\n**JE:** `{je_prog}/{je_t}`\n"
                       f"*Tasks:* {desc}")

        new_state = db.get_state(ctx.channel.id)
        await self.send_dyp_ping(ctx.channel, new_state)

    @commands.command(name="roles")
    async def check_roles(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        roles = [role.mention for role in member.roles if role.name != "@everyone"]
        if not roles: return await ctx.send(f"{member.mention} has no roles.")
        await ctx.send(f"**{member.display_name}** has {len(roles)} roles:\n{', '.join(roles)}")

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
            embed.add_field(name="Tasks", value=state['task_desc'], inline=False)
            
            if state['ofa_holder']:
                if state['ofa_done']: ofa_status = f"✅ Completed by <@{state['ofa_holder']}> (Target: {state['ofa_target']})"
                else: ofa_status = f"⏳ In Progress by <@{state['ofa_holder']}> (Target: {state['ofa_target']})"
            else: ofa_status = "Available ✅"
            embed.add_field(name="One For All (OFA)", value=ofa_status, inline=False)
            
            dyp_status = "✅ Completed" if state['dyp_progress'] >= state['dyp_target'] else f"{state['dyp_progress']}/{state['dyp_target']} pending"
            embed.add_field(name="Do Your Part (DYP)", value=dyp_status, inline=True)
            
            je_status = "✅ Completed" if state['je_done'] else f"{state['je_progress']}/{state['je_target']} contributions"
            embed.add_field(name="Joint Effort (JE)", value=je_status, inline=True)
            
            await ctx.send(embed=embed)
            return

        if action == "clear":
            if not is_admin: return
            db.update_state(ctx.channel.id, lap=0, node=0, ofa_target=None, dyp_progress=0, dyp_target=0, je_progress="0", task_desc="No Active Node")
            db.clear_pending_ofa_for_channel(ctx.channel.id)
            db.reset_dyp_contributors(ctx.channel.id)
            await ctx.send("🧹 **Node status cleared.** Bot will not ping until next `!race newnode`.")
            return

        if action == "ping":
            if not is_admin: return
            await self.send_dyp_ping(ctx.channel, state)
            return

        if action == "newnode":
            if not is_admin: return

            if ctx.message.attachments and len(ctx.message.attachments) > 1:
                await ctx.send("⚠️ You uploaded multiple images. I will only process the **first** one.")
            
            if ctx.message.attachments:
                await ctx.send("🔍 Scanning attached image...")
                ocr_data = await process_race_image(await ctx.message.attachments[0].read())
                if ocr_data:
                    desc = f"OFA: {ocr_data['ofa']['task']} | DYP: {ocr_data['dyp']['task']} | JE: {ocr_data['je']['task']}"
                    await self.setup_node(
                        ctx, state, 
                        ocr_data['ofa']['target'], 
                        ocr_data['dyp']['target'], 
                        ocr_data['je']['target'], 
                        desc,
                        dyp_prog=ocr_data['dyp']['progress'],
                        je_prog=ocr_data['je']['progress']
                    )
                    return
                else:
                    await ctx.send("⚠️ OCR failed. Use manual input.")

            if args:
                try:
                    (ofa_t, ofa_d), (dyp_r, dyp_d), (je_t, je_d) = parse_node_args(args)
                    desc = f"OFA: {ofa_d} | DYP: {dyp_d} | JE: {je_d}"
                    await self.setup_node(ctx, state, ofa_t, int(dyp_r), je_t, desc)
                except ValueError:
                    await ctx.send("❌ Invalid format. Use `!race newnode 53 hatch | 13 breed | 5 craft`")
                return

            if hasattr(self.bot, 'node_sheet'):
                n_node = state['node'] + 1
                n_lap = state['lap']
                if n_node > config.NODES_PER_LAP:
                    n_node = 1
                    n_lap += 1
                node_key = f"{n_lap}.{n_node}"
                if node_key in self.bot.node_sheet:
                    data = self.bot.node_sheet[node_key]
                    desc = f"OFA: {data['ofa']['task']} | DYP: {data['dyp']['task']} | JE: {data['je']['task']}"
                    await self.setup_node(ctx, state, data['ofa']['target'], int(data['dyp']['target']), data['je']['target'], desc)
                    return
                else:
                    await ctx.send(f"⚠️ Node `{node_key}` not found in the loaded sheet. Please attach an image or use manual input.")
                    return

            await ctx.send("❌ Please attach an image, provide text, or load the node sheet.")

        elif action == "set":
            if not is_admin: return
            parts = args.split(maxsplit=1)
            if parts[0].lower() == "desc":
                db.update_state(ctx.channel.id, task_desc=parts[1])
                await ctx.send(f"✅ Task description set to: `{parts[1]}`")
            elif len(parts) >= 2 and parts[0].lower() in ("dyp", "je", "ofa"):
                if parts[0].lower() == "ofa":
                    db.update_state(ctx.channel.id, ofa_target=parts[1])
                    await ctx.send(f"✅ OFA target set to {parts[1]}.")
                elif parts[0].lower() == "dyp":
                    db.update_state(ctx.channel.id, dyp_target=int(parts[1]))
                    await ctx.send(f"✅ DYP target set to {parts[1]}.")
                else:
                    db.update_state(ctx.channel.id, je_target=parts[1])
                    await ctx.send(f"✅ JE target set to {parts[1]}.")

        elif action == "ofa":
            if args and args.lower() == "done":
                if not state['ofa_holder'] or (ctx.author.id != state['ofa_holder'] and not is_admin): return
                db.update_state(ctx.channel.id, ofa_done=1)
                await ctx.send(f"🎉 **OFA marked as COMPLETED** by <@{state['ofa_holder']}>!")
                return
            if state['ofa_holder'] or db.get_pending_ofa_by_channel(ctx.channel.id): return
            
            role_pings = " ".join([f"<@&{rid}>" for rid in config.OFA_PING_ROLES.get(ctx.channel.id, [])])
            msg = await ctx.send(f"🏆 {ctx.author.mention} is claiming **OFA**! {role_pings}\nReact ✅ to approve.")
            await msg.add_reaction("✅"); await msg.add_reaction("❌")
            db.add_pending_ofa(msg.id, ctx.channel.id, ctx.author.id)

        elif action in ("dyp", "dmp"):
            if args and args.lower() == "skip":
                if not is_admin: return
                db.update_state(ctx.channel.id, dyp_progress=state['dyp_target'])
                await ctx.send(f"⏭️ **DYP skipped by staff!**")
                return
                
            # If they use !race dyp done, or !race dmp, or !race dyp +1
            if action == "dmp" or (args and args.lower() == "done"):
                if db.increment_dyp(ctx.channel.id, ctx.author.id):
                    new_state = db.get_state(ctx.channel.id)
                    await ctx.send(f"✅ DYP logged. Pending: `{new_state['dyp_progress']}/{new_state['dyp_target']}`")
                else:
                    await ctx.send("❌ You have already contributed for this node, or the target is reached.")
                    
            elif args and args.startswith("+"):
                amount = int(parse_amount(args[1:]))
                new_progress = min(state['dyp_progress'] + amount, state['dyp_target'])
                
                # Update the progress by the exact amount
                db.update_state(ctx.channel.id, dyp_progress=new_progress)
                
                # Silently mark them as a contributor WITHOUT adding +1
                db.cursor.execute("INSERT OR IGNORE INTO dyp_contributors (channel_id, user_id) VALUES (?, ?)", (ctx.channel.id, ctx.author.id))
                db.conn.commit()
                
                if new_progress >= state['dyp_target']:
                    await ctx.send(f"🎉 **DYP Target Reached!** ({new_progress}/{state['dyp_target']})")
                else:
                    await ctx.send(f"✅ DYP logged. Pending: `{new_progress}/{state['dyp_target']}`")
            else:
                await ctx.send("Usage: `!race dyp done`, `!race dmp`, or `!race dyp +X`")

        elif action == "je":
            if args and args.lower() == "skip":
                if not is_admin: return
                db.update_state(ctx.channel.id, je_done=1)
                await ctx.send("⏭️ **JE skipped!**")
            elif args and args.lower() == "done":
                db.update_state(ctx.channel.id, je_done=1, je_progress=str(state['je_target']))
                await ctx.send(f"🎉 **JE completed** by {ctx.author.mention}!")
            elif args and args.startswith("+"):
                amount = parse_amount(args[1:])
                curr = parse_amount(state['je_progress'])
                target = parse_amount(state['je_target'])
                new_val = min(curr + amount, target)
                db.update_state(ctx.channel.id, je_progress=format_amount(new_val))
                await ctx.send(f"✅ JE logged. Progress: `{format_amount(new_val)}/{state['je_target']}`")
                if new_val >= target:
                    db.update_state(ctx.channel.id, je_done=1)
                    await ctx.send("🎉 **JE Target Reached!**")

    @commands.command(name="dmp")
    @is_race_channel()
    async def dmp(self, ctx):
        state = db.get_state(ctx.channel.id)
        is_admin = any(role.id in config.ADMIN_ROLES for role in ctx.author.roles)
        if not state['event_active'] and not is_admin: return
            
        if db.increment_dyp(ctx.channel.id, ctx.author.id):
            state = db.get_state(ctx.channel.id)
            await ctx.send(f"✅ DYP logged for {ctx.author.mention}. Pending: `{state['dyp_progress']}/{state['dyp_target']}`")
        else:
            await ctx.send("❌ You have already contributed for this node, or the target is reached.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id: return
        pending = db.get_pending_ofa(payload.message_id)
        if not pending or payload.channel_id != pending['channel_id']: return

        member = payload.member
        if not member or not any(role.id in config.ADMIN_ROLES for role in member.roles): return

        channel = self.bot.get_channel(payload.channel_id)
        if str(payload.emoji) == "✅":
            if db.get_state(payload.channel_id)['ofa_holder']:
                db.delete_pending_ofa(payload.message_id)
                return
            db.update_state(payload.channel_id, ofa_holder=pending['user_id'], ofa_done=0)
            db.clear_pending_ofa_for_channel(payload.channel_id)
            await channel.send(f"✅ OFA locked for <@{pending['user_id']}>!")
        elif str(payload.emoji) == "❌":
            await channel.send(f"❌ OFA claim denied.")
        db.delete_pending_ofa(payload.message_id)

    @commands.command(name="ignore")
    @is_race_channel()
    async def ignore(self, ctx, time_str: str):
        match = re.fullmatch(r"(\d+)([mh])", time_str.lower())
        if not match: return await ctx.send("❌ Invalid format. Use `!ignore 1h`.")
        amount, unit = int(match.group(1)), match.group(2)
        seconds = amount * 60 if unit == 'm' else amount * 3600
        db.add_user_mute(ctx.author.id, seconds)
        await ctx.send(f"🔇 {ctx.author.mention} muted for {amount}{unit}.")

    @commands.command(name="unignore")
    @is_race_channel()
    async def unignore(self, ctx):
        db.remove_user_mute(ctx.author.id)
        await ctx.send(f"🔊 {ctx.author.mention} unmuted.")

    @tasks.loop(minutes=15)
    async def reminder_loop(self):
        for channel_id in config.RACE_CHANNELS:
            state = db.get_state(channel_id)
            if not state['event_active']: continue
            
            if state['node'] == 0 or state['dyp_target'] == 0: continue
            
            channel = self.bot.get_channel(channel_id)
            if not channel: continue

            if state['dyp_progress'] < state['dyp_target']:
                await self.send_dyp_ping(channel, state)

    @reminder_loop.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(RaceManager(bot))
