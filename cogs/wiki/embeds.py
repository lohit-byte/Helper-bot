from __future__ import annotations

import asyncio
from urllib.parse import quote, urlencode
import discord
from discord.ext import commands

WIKI_URL = "https://monsterlegends.fandom.com/wiki/"
SMALL_WORDS = {"of", "the", "and", "a", "an", "in", "on", "de", "la"}

def normalize_title(name: str) -> str:
    fixed = []
    for i, word in enumerate(name.strip()[:150].split()):
        if i > 0 and word.lower() in SMALL_WORDS:
            fixed.append(word.lower())
        else:
            fixed.append(word[:1].upper() + word[1:])
    return " ".join(fixed)

def page_url(title: str) -> str:
    return WIKI_URL + quote(title.replace(" ", "_"), safe="_,!:-.'")

def wiki_search_url(query: str) -> str:
    params = {"query": query.strip()[:200], "go": "Go"}
    return f"{WIKI_URL}Special:Search?{urlencode(params)}"

async def send_direct_link(ctx: commands.Context, name: str) -> None:
    title = normalize_title(name)
    embed = discord.Embed(
        title=title, url=page_url(title),
        description=f"[Open the monster page]({page_url(title)})\nWrong page? [Search the wiki]({wiki_search_url(name)})",
        color=discord.Color.orange(),
    )
    embed.set_footer(text="Stats unavailable right now, link only")
    await ctx.send(embed=embed)

async def send_search_fallback(ctx: commands.Context, name: str, reason: str) -> None:
    safe_name = discord.utils.escape_markdown(name.strip()[:150])
    await ctx.send(f"{reason} [Search the Monster Legends Wiki for {safe_name}]({wiki_search_url(name)})")

def stats_block(ranks: list[tuple]) -> str:
    heads = ["Power", "Life", "Speed", "Stam"]
    widths = [max(len(h), *(len(r[i]) for r in ranks)) for i, h in enumerate(heads)]
    lines = ["   " + " ".join(h.rjust(w) for h, w in zip(heads, widths))]
    for i, row in enumerate(ranks):
        lines.append(f"{i}★ " + " ".join(v.rjust(w) for v, w in zip(row, widths)))
    return "```\n" + "\n".join(lines) + "\n```"

def add_fields(embed, page: dict) -> None:
    def safe_add(name, value, inline=False):
        if value and str(value).strip() and len(embed.fields) < 25:
            embed.add_field(name=name, value=str(value)[:1024], inline=inline)

    overview = [" / ".join(page.get("rarity", [])), ", ".join(page.get("elements", [])), page.get("role", "")]
    overview = " • ".join(x for x in overview if x)
    safe_add("Overview", overview, inline=False)

    econ = []
    if page.get("breed"): econ.append(f"Breed {page['breed']}")
    if page.get("hatch"): econ.append(f"Hatch {page['hatch']}")
    if page.get("sell"): econ.append(f"Sell {page['sell']}")
    if page.get("price"): econ.append(f"Price {page['price']}")
    if econ: safe_add("Breeding & value", " • ".join(econ), inline=False)

    if page.get("trait"):
        value = page["trait"][:900]
        if page.get("max_gpm"): value += f"\nMax GPM: {page['max_gpm']}"
        safe_add("Traits", value, inline=False)

    gear = []
    if page.get("books"): gear.append("Books: " + ", ".join(page["books"]))
    if page.get("relics"): gear.append("Relics: " + ", ".join(page["relics"]))
    if gear: safe_add("Books & relics", "\n".join(gear), inline=False)

    if page.get("ranks"): safe_add("Stats by rank", stats_block(page["ranks"][:6]), inline=False)

    if page.get("combos"):
        lines = "\n".join(f"• {c}" for c in page["combos"][:6])
        safe_add("How to get it", lines, inline=False)
    elif page.get("obtain"):
        safe_add("How to get it", page["obtain"], inline=False)

async def build_embed(wiki, query: str) -> discord.Embed | None:
    titles = await wiki.search_titles(query, 5)
    if not titles: return None

    title = next((t for t in titles if t.lower() == query.lower()), titles[0])

    summary, page = await asyncio.gather(
        wiki.get_summary(title), wiki.get_page_data(title), return_exceptions=True
    )
    if isinstance(summary, Exception): raise summary
    if not summary: return None
    if isinstance(page, Exception):
        print(f"[wiki] page parse failed for {title!r}: {page!r}")
        page = {}

    description = page.get("description", "")
    if len(description) > 400: description = description[:397].rstrip() + "..."

    embed = discord.Embed(
        title=summary["title"], url=page_url(summary["title"]),
        description=description or "Tap the title to open the full page.",
        color=discord.Color.orange(),
    )
    if summary["image"]: embed.set_thumbnail(url=summary["image"])

    add_fields(embed, page)
    embed.set_footer(text="Source: Monster Legends Wiki")
    return embed