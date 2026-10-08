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
            # Query both wikis concurrently
            results = await asyncio.gather(
                build_embed(self.wiki, name, site_name="Monster Legends Wiki"),
                build_embed(self.comp_wiki, name, site_name="Competitive Wiki"),
                return_exceptions=True,
            )
            
            sent_any = False
            errors = 0
            for res in results:
                if isinstance(res, Exception):
                    errors += 1
                    print(f"[wiki] error: {res!r}")
                elif res is not None:
                    await ctx.send(embed=res)
                    sent_any = True

            if not sent_any:
                if errors == len(results):
                    # Both wikis failed (network error, etc.)
                    await send_direct_link(ctx, name)
                else:
                    # Neither wiki had the page
                    await send_search_fallback(
                        ctx, name,
                        f"No matching page was found on either wiki for **{escape_markdown(name)}**.",
                    )
                    
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
    await bot.add_cog(WikiCog(bot))