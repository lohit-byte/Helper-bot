from __future__ import annotations

import asyncio
from urllib.parse import quote, urlencode
import discord
from discord.ext import commands

SMALL_WORDS = {"of", "the", "and", "a", "an", "in", "on", "de", "la"}

def normalize_title(name: str) -> str:
    fixed = []
    for i, word in enumerate(name.strip()[:150].split()):
        if i > 0 and word.lower() in SMALL_WORDS:
            fixed.append(word.lower())
        else:
            fixed.append(word[:1].upper() + word[1:])
    return " ".join(fixed)

def page_url(base_url: str, title: str) -> str:
    return base_url + "/wiki/" + quote(title.replace(" ", "_"), safe="_,!:-.'")

def wiki_search_url(base_url: str, query: str) -> str:
    params = {"query": query.strip()[:200], "go": "Go"}
    return f"{base_url}/wiki/Special:Search?{urlencode(params)}"

async def send_direct_link(ctx: commands.Context, name: str) -> None:
    title = normalize_title(name)
    embed = discord.Embed(
        title=title,
        url=page_url("https://monsterlegends.fandom.com", title),
        description=f"Could not reach the wiki API. [Open the monster page]({page_url('https://monsterlegends.fandom.com', title)})",
        color=discord.Color.orange(),
    )
    embed.set_footer(text="Stats unavailable right now, link only")
    await ctx.send(embed=embed)

async def send_search_fallback(ctx: commands.Context, name: str, reason: str) -> None:
    safe_name = discord.utils.escape_markdown(name.strip()[:150])
    search_url = wiki_search_url("https://monsterlegends.fandom.com", name)
    await ctx.send(f"{reason} [Search the Monster Legends Wiki for {safe_name}]({search_url})")

def stats_block(ranks: list[tuple]) -> str:
    if not ranks:
        return "No stats available."
    
    lines = []
    for i, row in enumerate(ranks[:5]):
        vals = list(row) + ["N/A"] * (4 - len(row)) if len(row) < 4 else list(row[:4])
        lines.append(f"{i}★ | P: {vals[0]} | L: {vals[1]} | S: {vals[2]} | St: {vals[3]}")
    
    return "```\n" + "\n".join(lines) + "\n```"

def add_fields(embed, page: dict, base_url: str) -> None:
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
        value = page["trait"][:400] + "..." if len(page["trait"]) > 400 else page["trait"]
        if page.get("max_gpm"): value += f"\nMax GPM: {page['max_gpm']}"
        safe_add("Traits", value, inline=False)

    gear = []
    if page.get("books"): gear.append("Books: " + ", ".join(page["books"]))
    if page.get("relics"): gear.append("Relics: " + ", ".join(page["relics"]))
    if gear: safe_add("Books & relics", "\n".join(gear), inline=False)

    if page.get("ranks"): 
        safe_add("Stats by rank", stats_block(page["ranks"]), inline=False)

    if page.get("combos"):
        lines = "\n".join(f"• {c}" for c in page["combos"][:6])
        safe_add("How to get it", lines, inline=False)
    elif page.get("obtain"):
        safe_add("How to get it", page["obtain"], inline=False)

async def build_embed(wiki, query: str, site_name: str) -> discord.Embed | None:
    titles = await wiki.search_titles(query, 5)
    if not titles: return None

    title = next((t for t in titles if t.lower() == query.lower()), titles[0])
    base_url = "https://monster-legends-competitive.fandom.com" if "Competitive" in site_name else "https://monsterlegends.fandom.com"

    summary, page = await asyncio.gather(
        wiki.get_summary(title), wiki.get_page_data(title), return_exceptions=True
    )
    if isinstance(summary, Exception): raise summary
    if not summary: return None
    if isinstance(page, Exception):
        print(f"[{site_name}] page parse failed for {title!r}: {page!r}")
        page = {}

    description = page.get("description", "")
    if len(description) > 400: description = description[:397].rstrip() + "..."

    embed = discord.Embed(
        title=f"{summary['title']} ({site_name})",
        url=page_url(base_url, summary["title"]),
        description=description or "Tap the title to open the full page.",
        color=discord.Color.orange() if "Competitive" not in site_name else discord.Color.purple(),
    )
    if summary["image"]: embed.set_thumbnail(url=summary["image"])

    add_fields(embed, page, base_url)
    embed.set_footer(text=f"Source: {site_name}")
    return embed