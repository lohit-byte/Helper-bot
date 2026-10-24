import discord
from discord.ext import commands
import config
from utils.database import db

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="race_event")
    @commands.has_any_role(*config.ADMIN_ROLES)
    async def toggle_event(self, ctx, state: str):
        if ctx.channel.id not in config.RACE_CHANNELS:
            await ctx.send("❌ Use this in a race channel.")
            return
        state = state.lower()
        if state not in ("on", "off"):
            await ctx.send("❌ Usage: `!race_event on` or `!race_event off`")
            return
        
        is_active = 1 if state == "on" else 0
        db.update_state(ctx.channel.id, event_active=is_active)
        
        if is_active: await ctx.send("✅ **Race event is now ACTIVE.** Commands are enabled.")
        else: await ctx.send("⏸️ **Race event is now DISABLED.** Commands are paused.")

    @commands.command(name="race_hold")
    @commands.has_any_role(*config.ADMIN_ROLES)
    async def hold_ofa(self, ctx, member: discord.Member):
        db.clear_pending_ofa_for_channel(ctx.channel.id)
        db.update_state(ctx.channel.id, ofa_holder=member.id, ofa_done=0)
        await ctx.send(f"🔒 OFA manually held for {member.mention} by staff.")

    @commands.command(name="race_release")
    @commands.has_any_role(*config.ADMIN_ROLES)
    async def release_ofa(self, ctx):
        db.update_state(ctx.channel.id, ofa_holder=None, ofa_done=0)
        await ctx.send("🔓 OFA released. It is now available for anyone to claim.")

    @commands.command(name="race_reset")
    @commands.has_any_role(*config.ADMIN_ROLES)
    async def reset_node(self, ctx):
        db.update_state(ctx.channel.id, lap=0, node=0, ofa_holder=None, ofa_target=None, ofa_done=0, dyp_progress=0, dyp_target=10, je_progress="0", je_target="4", je_done=0, task_desc="Race Task")
        db.clear_pending_ofa_for_channel(ctx.channel.id)
        db.reset_dyp_contributors(ctx.channel.id)
        await ctx.send("🔄 Race node fully reset. Next `!race newnode` will start Lap 0.1.")

    @commands.command(name="race_set")
    @commands.has_any_role(*config.ADMIN_ROLES)
    async def set_node(self, ctx, lap: int, node: int):
        if not (1 <= node <= config.NODES_PER_LAP) or lap < 0:
            await ctx.send(f"❌ Lap must be ≥ 0 and Node must be between 1 and {config.NODES_PER_LAP}.")
            return
        db.update_state(ctx.channel.id, lap=lap, node=node)
        await ctx.send(f"✅ Node manually set to **Lap {lap}.{node}**.")

    @commands.command(name="race_bypass")
    @commands.has_any_role(*config.ADMIN_ROLES)
    async def bypass_dyp(self, ctx):
        state = db.get_state(ctx.channel.id)
        if state['dyp_progress'] >= state['dyp_target']: return await ctx.send("❌ DYP target already reached.")
        db.update_state(ctx.channel.id, dyp_progress=state['dyp_progress'] + 1)
        state = db.get_state(ctx.channel.id)
        await ctx.send(f"⚙️ Staff bypass: DYP progress is now `{state['dyp_progress']}/{state['dyp_target']}`.")
        if state['dyp_progress'] >= state['dyp_target']: await ctx.send("🎉 **DYP Target Reached!**")

    @commands.command(name="race_bypass_je")
    @commands.has_any_role(*config.ADMIN_ROLES)
    async def bypass_je(self, ctx):
        state = db.get_state(ctx.channel.id)
        if state['je_done']: return await ctx.send("❌ JE is already completed.")
        db.update_state(ctx.channel.id, je_progress=state['je_target'], je_done=1)
        await ctx.send(f"🎉 **JE Target Reached via bypass! Node complete.**")

    @commands.command(name="help")
    async def custom_help(self, ctx):
        embed = discord.Embed(title="🤖 Monster Legends Race Bot Guide", color=discord.Color.gold())
        
        embed.add_field(name="🔹 General Commands", value="`!race` - Shows current node status\n`!ignore (1h/30m)` - Mutes pings for yourself\n`!unignore` - Unmutes yourself", inline=False)
        embed.add_field(name="🔹 Wiki Commands", value="`!monster <name>` or `/monster <name>` - Look up a monster", inline=False)
        
        embed.add_field(
            name="🔹 Race Tasks", 
            value="`!race OFA` - Claims One For All\n`!race OFA done` - Marks your OFA as completed\n`!race dyp done` / `!dmp` - Logs your DYP\n`!race je +1` / `+500k` / `done` - Updates JE", 
            inline=False
        )
        
        embed.add_field(
            name="🔹 Staff Only", 
            value="`!race_event on/off` - Enable/Disable race\n"
                  "`!race newnode <text or image>` - Advance node via OCR or text\n"
                  "`!race clear` - Wipes current node if broken\n"
                  "`!race ping` - Forces a DYP ping immediately\n"
                  "`!race_hold @user` - Locks OFA\n"
                  "`!race_release` - Releases OFA\n"
                  "`!race_reset` - Resets to Lap 0.1\n"
                  "`!race_set <lap> <node>` - Manually set lap/node\n"
                  "`!race_bypass` - Manually +1 DYP\n"
                  "`!race set desc <text>` - Set task description", 
            inline=False
        )
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Admin(bot))
