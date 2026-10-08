import discord
from discord.ext import commands
import config
import asyncio
import traceback

# ⚠️ Ensure Server Members Intent is enabled in the Discord Developer Portal
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix=["!", "?"], intents=intents, help_command=None)

@bot.event
async def setup_hook():
    await bot.tree.sync()
    print("Synced slash commands.")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingAnyRole):
        await ctx.send("❌ Staff only.")
    elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        await ctx.send(f"❌ Check your command: `{ctx.prefix}help`")
    elif isinstance(error, commands.CheckFailure):
        pass
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        # Print full traceback for debugging
        traceback.print_exception(type(error), error, error.__traceback__)
        await ctx.send("⚠️ An unexpected error occurred. Check logs for details.")

async def main():
    async with bot:
        # ONLY load the Info, Race, and Admin modules to prevent clashing with your warbot
        cogs_to_load = [
            "cogs.race",
            "cogs.admin",
            "cogs.wiki.cog"
        ]
        for ext in cogs_to_load:
            try:
                await bot.load_extension(ext)
                print(f"Loaded {ext}")
            except Exception as e:
                print(f"❌ Failed to load {ext}: {e}")
                traceback.print_exc()
                
        await bot.start(config.TOKEN)

if __name__ == "__main__":
    asyncio.run(main())