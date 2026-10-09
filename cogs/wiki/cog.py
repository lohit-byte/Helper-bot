import traceback
import asyncio
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from .embeds import (
    fetch_both,
    build_pages,
    MonsterView,
    remember_pages,
    send_direct_link,
    send_search_fallback,
)
from .client import WikiClient

class WikiCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Main Fandom Wiki
        self.wiki = WikiClient("https://monsterlegends.fandom.com")
        # Competitive Fandom Wiki
        self.comp_wiki = WikiClient("https://monster-legends-competitive.fandom.com")

    async def cog_unload(self) -> None:
        await self.wiki.close()
        await self.comp_wiki.close()

    async def _lookup(self, ctx: commands.Context, name: str):
        name = name.strip()[:100]
        await ctx.defer()

        try:
            # Query both wikis, merge into ONE message (tabs switch between sections)
            main, comp, errors = await fetch_both(self.wiki, self.comp_wiki, name)

            if not main and not comp:
                if errors:
                    # Network/API trouble, not a "not found"
                    await send_direct_link(ctx, name)
                else:
                    await send_search_fallback(
                        ctx, name,
                        f"No matching page was found on either wiki for **{escape_markdown(name)}**.",
                    )
                return

            pages = build_pages(main, comp)
            if len(pages) == 1:
                await ctx.send(embed=pages["overview"])
                return

            msg = await ctx.send(embed=pages["overview"], view=MonsterView(list(pages)))
            remember_pages(msg.id, pages, ctx.author.id)

        except Exception:
            traceback.print_exc()
            await send_direct_link(ctx, name)

    async def _suggest(self, current: str):
        if len(current) < 2: return []
        try:
            # Autocomplete only uses the primary wiki (Discord limits to 25 choices)
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
    bot.add_view(MonsterView())  # re-attaches the buttons of old messages after a restart
    await bot.add_cog(WikiCog(bot))
