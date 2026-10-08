import traceback
import asyncio
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from .embeds import build_embed, send_direct_link, send_search_fallback
from .client import WikiClient

class WikiCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.wiki = WikiClient()

    async def cog_unload(self) -> None:
        await self.wiki.close()

    async def _lookup(self, ctx: commands.Context, name: str):
        name = name.strip()[:100]  # Prevent 2000-character limit issues
        await ctx.defer()
        try:
            embed = await build_embed(self.wiki, name)
        except Exception:
            traceback.print_exc()
            await send_direct_link(ctx, name)
            return

        if embed is None:
            await send_search_fallback(ctx, name, f"No matching page was found for **{escape_markdown(name)}**.")
        else:
            await ctx.send(embed=embed)

    async def _suggest(self, current: str):
        if len(current) < 2: 
            return []
        try:
            # Discord gives autocomplete 3 seconds, wait_for prevents 10062 Unknown Interaction
            titles = await asyncio.wait_for(self.wiki.search_titles(current, 10), timeout=2.5)
        except Exception:
            return []
        return [app_commands.Choice(name=t[:100], value=t[:100]) for t in titles]

    @commands.hybrid_command(name="monster", description="Look up a Monster Legends monster")
    @app_commands.describe(name="Monster name, e.g. Gigantus")
    async def monster(self, ctx: commands.Context, *, name: str):
        await self._lookup(ctx, name)

    @monster.autocomplete("name")
    async def monster_autocomplete(self, interaction, current: str):
        return await self._suggest(current)

async def setup(bot: commands.Bot):
    await bot.add_cog(WikiCog(bot))