from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote, urlencode
import discord
from discord.ext import commands

MAIN_URL = "https://monsterlegends.fandom.com"
COMP_URL = "https://monster-legends-competitive.fandom.com"
SMALL_WORDS = {"of", "the", "and", "a", "an", "in", "on", "de", "la"}

# Mobile Discord squeezes ALL embed text into ~60% width when a thumbnail is set.
# Keep this False so text uses the full width; the monster image shows as a small icon instead.
USE_THUMBNAIL = False
OVERVIEW_CHARS = 900
EMBED_COLOR = discord.Color.orange()
EMPTY_VALUES = {"", "n/a", "-", "—", "none"}
TABS = (
    ("overview", "Overview"),
    ("stats", "Stats & traits"),
    ("moveset", "Moveset"),
    ("matchups", "Allies & counters"),
)

# ----------------------------------------------------------------------------- basics

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
        url=page_url(MAIN_URL, title),
        description=f"Could not reach the wiki API. [Open the monster page]({page_url(MAIN_URL, title)})",
        color=discord.Color.orange(),
    )
    embed.set_footer(text="Stats unavailable right now, link only")
    await ctx.send(embed=embed)

async def send_search_fallback(ctx: commands.Context, name: str, reason: str) -> None:
    safe_name = discord.utils.escape_markdown(name.strip()[:150])
    search_url = wiki_search_url(MAIN_URL, name)
    await ctx.send(f"{reason} [Search the Monster Legends Wiki for {safe_name}]({search_url})")

# ----------------------------------------------------------------------------- fetching

async def fetch_wiki(wiki, query: str) -> dict | None:
    """Find the page on one wiki. Returns {"title", "image", "page"} or None if not found."""
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
    return {"title": summary["title"], "image": summary["image"], "page": page}

async def fetch_both(main_wiki, comp_wiki, query: str):
    """Query both wikis. Returns (main, comp, errors). Makes sure both describe the same monster."""
    results = await asyncio.gather(
        fetch_wiki(main_wiki, query), fetch_wiki(comp_wiki, query), return_exceptions=True
    )
    errors = [r for r in results if isinstance(r, Exception)]
    for e in errors: print(f"[wiki] error: {e!r}")
    main, comp = [None if isinstance(r, Exception) else r for r in results]

    # Fuzzy search can return different monsters on each wiki: re-ask the competitive wiki by the main title.
    if main and comp and main["title"].lower() != comp["title"].lower():
        try:
            comp = await fetch_wiki(comp_wiki, main["title"])
        except Exception as e:
            print(f"[wiki] comp retry failed: {e!r}")
            comp = None
        if comp and comp["title"].lower() != main["title"].lower():
            comp = None
    return main, comp, errors

# ----------------------------------------------------------------------------- text helpers

def clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit: return text
    cut = text[: limit - 1]
    best = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if best > limit * 0.6:
        return cut[: best + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;:- ") + "…"

def bullets(lines: list[str], max_items: int = 5, max_len: int = 110, budget: int = 1000) -> str:
    out: list[str] = []
    used = 0
    shown = 0
    for line in lines[:max_items]:
        line = clip(line.lstrip("• ").strip(), max_len)
        if not line: continue
        row = f"• {line}"
        if used + len(row) + 1 > budget: break
        out.append(row)
        used += len(row) + 1
        shown += 1
    rest = len(lines) - shown
    if out and rest > 0:
        more = f"*+{rest} more on the wiki*"
        if used + len(more) <= 1024: out.append(more)
    return "\n".join(out)

def find_section(sections: list[dict], *names: str) -> list[str]:
    for s in sections:
        if s["title"].lower() in names and s["lines"]:
            return s["lines"]
    return []

def subsections(sections: list[dict], parent: str) -> list[dict]:
    out, inside, plevel = [], False, 0
    for s in sections:
        if not inside:
            if s["title"].lower() == parent:
                inside, plevel = True, s["level"]
            continue
        if s["level"] <= plevel: break
        out.append(s)
    return out

TIER_RE = re.compile(r"(?<![A-Za-z])([SABCDEF][+-]{0,2})(?![A-Za-z])")

def tier_of(items: list[str] | None) -> str:
    for it in items or []:
        m = TIER_RE.search(it)
        if m: return m.group(1)
    return ""

def stats_block(ranks: list[tuple]) -> str:
    """Compact monospace table, ~30 chars wide so it fits a phone without wrapping."""
    rows = []
    for i, row in enumerate(ranks[:8]):
        vals = (list(row) + ["-"] * 4)[:4]
        rows.append([f"{i}★"] + vals)
    heads = ["", "POW", "LIFE", "SPD", "STA"]
    widths = [max(len(heads[c]), *(len(r[c]) for r in rows)) for c in range(5)]
    def fmt(r): return " ".join(v.rjust(widths[c]) for c, v in enumerate(r))
    return "```\n" + "\n".join([fmt(heads)] + [fmt(r) for r in rows]) + "\n```"

def good(value) -> bool:
    return bool(value) and str(value).strip().lower() not in EMPTY_VALUES

# ----------------------------------------------------------------------------- embed building

def build_pages(main: dict | None, comp: dict | None) -> dict[str, discord.Embed]:
    """One embed per tab, built from BOTH wikis. Tabs with no data are left out."""
    mp = main["page"] if main else {}
    cp = comp["page"] if comp else {}
    base = main or comp
    name = base["title"]
    image = (main or {}).get("image") or (comp or {}).get("image")
    main_link = page_url(MAIN_URL, main["title"]) if main else None
    comp_link = page_url(COMP_URL, comp["title"]) if comp else None
    url = main_link or comp_link
    sources = " • ".join(n for n, x in (("Monster Legends Wiki", main), ("Competitive Wiki", comp)) if x)
    cinfo = cp.get("info", {})
    csecs = cp.get("sections", [])

    def new(tab_label: str, description: str = "") -> discord.Embed:
        e = discord.Embed(title=name, url=url, description=description or None, color=EMBED_COLOR)
        if image:
            if USE_THUMBNAIL: e.set_thumbnail(url=image)
            else: e.set_author(name=tab_label, icon_url=image)
        elif not USE_THUMBNAIL:
            e.set_author(name=tab_label)
        e.set_footer(text=f"Sources: {sources}")
        return e

    def add(e: discord.Embed, field_name: str, value: str, inline: bool = False) -> None:
        if value and value.strip() and len(e.fields) < 25:
            e.add_field(name=field_name[:256], value=value[:1024], inline=inline)

    def finish(e: discord.Embed) -> discord.Embed:
        while len(e) > 5800 and e.fields:
            e.remove_field(len(e.fields) - 1)
        return e

    pages: dict[str, discord.Embed] = {}

    # ---- Overview -------------------------------------------------------------------------
    ov_text = " ".join(l for l in find_section(csecs, "overview") if not l.startswith("• "))
    desc = clip(ov_text or cp.get("description") or mp.get("description", ""), OVERVIEW_CHARS)
    if ov_text and len(ov_text) > OVERVIEW_CHARS and comp_link:
        desc += f" [Read more]({comp_link})"
    e = new("Overview", desc or "Tap the title to open the full page.")

    rarity = mp.get("rarity") or cp.get("rarity") or []
    elements = mp.get("elements") or cp.get("elements") or []
    role = mp.get("role") or cp.get("role") or ""
    profile = " • ".join(x for x in (" / ".join(rarity), ", ".join(elements), role) if x)
    rank, era = tier_of(cinfo.get("rank")), tier_of(cinfo.get("unified era rank"))
    tiers = " • ".join(x for x in (f"Rank **{rank}**" if rank else "", f"Unified Era **{era}**" if era else "") if x)
    add(e, "Profile", "\n".join(x for x in (profile, tiers) if x))
    add(e, "✅ Pros", bullets(find_section(csecs, "pros"), 5, 90, 600))
    add(e, "⚠️ Cons", bullets(find_section(csecs, "cons"), 5, 90, 600))
    links = " • ".join(f"[{n}]({u})" for n, u in (("Wiki", main_link), ("Competitive guide", comp_link)) if u)
    add(e, "Links", links)
    pages["overview"] = finish(e)

    # ---- Stats & traits ---------------------------------------------------------------------
    e = new("Stats & traits")
    econ = [f"{label} {mp[k]}" for label, k in (("Breed", "breed"), ("Hatch", "hatch"), ("Sell", "sell"), ("Price", "price")) if good(mp.get(k))]
    add(e, "Breeding & value", " • ".join(econ))

    if mp.get("ranks"):
        add(e, "Stats by rank", stats_block(mp["ranks"]))

    trait_lines = cinfo.get("trait") or []
    if trait_lines:
        value = bullets(trait_lines, 8, 80, 800)
    elif mp.get("trait"):
        value = clip(mp["trait"], 350)
    else:
        value = ""
    if value and mp.get("max_gpm"): value += f"\nMax GPM: {mp['max_gpm']}"
    add(e, "Traits", value)

    add(e, "Virtue", clip(" ".join(cinfo.get("virtue", [])), 200))

    books = mp.get("books") or []
    relics = mp.get("relics") or []
    gear = []
    if books: gear.append("**Books:** " + ", ".join(books))
    elif cinfo.get("books"): gear.append("**Books:** " + ", ".join(cinfo["books"]))
    if relics: gear.append("**Relics:** " + ", ".join(relics))
    elif cinfo.get("relics"): gear.append("**Relics:** " + " ".join(cinfo["relics"]))
    add(e, "Books & relics", "\n".join(gear))

    how = (mp.get("combos") or []) + (mp.get("obtain_lines") or [])
    if how:
        add(e, "How to get it", bullets(how, 6, 120, 700))
    elif mp.get("obtain") and re.search(r"[A-Za-z]", mp["obtain"]):
        add(e, "How to get it", clip(mp["obtain"], 250))
    if e.fields: pages["stats"] = finish(e)

    # ---- Moveset (competitive wiki) ---------------------------------------------------------
    moves = subsections(csecs, "recommended moveset")
    if moves:
        e = new("Moveset")
        notes: list[str] = []
        for s in moves:
            bl = [l for l in s["lines"] if l.startswith("• ")]
            notes += [l for l in s["lines"] if not l.startswith("• ")]
            if bl: add(e, s["title"], bullets(bl, 5, 140, 900))
        if notes:
            add(e, "Runes, relics & notes", bullets(notes, 4, 200, 900))
        if e.fields: pages["moveset"] = finish(e)

    # ---- Allies & counters (competitive wiki) -----------------------------------------------
    allies = find_section(csecs, "recommended allies")
    counters = find_section(csecs, "counters")
    if allies or counters:
        e = new("Allies & counters")
        add(e, "🤝 Recommended allies", bullets(allies, 4, 200, 950))
        add(e, "🛡️ Counters", bullets(counters, 4, 200, 950))
        if e.fields: pages["matchups"] = finish(e)

    return pages

# ----------------------------------------------------------------------------- persistent view

STORE_DIR = Path(__file__).resolve().parent / "monster_cache"
MAX_STORED = 300     # newest lookups kept on disk; older ones stop responding
OWNER_ONLY = False   # True = only the person who ran the command can switch tabs
_CACHE: OrderedDict = OrderedDict()

def remember_pages(message_id: int, pages: dict[str, discord.Embed], owner_id: int) -> None:
    _CACHE[message_id] = (pages, owner_id)
    while len(_CACHE) > 100: _CACHE.popitem(last=False)
    try:
        STORE_DIR.mkdir(exist_ok=True)
        payload = {"owner": owner_id, "pages": {k: e.to_dict() for k, e in pages.items()}}
        (STORE_DIR / f"{message_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        for old in sorted(STORE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)[:-MAX_STORED]:
            old.unlink(missing_ok=True)
    except Exception as e:
        print(f"[wiki] could not save pages: {e!r}")

def recall_pages(message_id: int):
    if message_id in _CACHE: return _CACHE[message_id]
    try:
        data = json.loads((STORE_DIR / f"{message_id}.json").read_text(encoding="utf-8"))
        result = ({k: discord.Embed.from_dict(v) for k, v in data["pages"].items()}, data.get("owner"))
        _CACHE[message_id] = result
        return result
    except Exception:
        return None, None

class MonsterView(discord.ui.View):
    """Buttons that swap the tab of ONE message, so everything stays in a single reply."""

    def __init__(self, available: list[str] | None = None, current: str = "overview"):
        super().__init__(timeout=None)
        for key, label in TABS:
            if available is not None and key not in available: continue
            btn = discord.ui.Button(
                label=label, custom_id=f"monster:{key}",
                style=discord.ButtonStyle.primary if key == current else discord.ButtonStyle.secondary,
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

    def _make_callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            pages, owner = recall_pages(interaction.message.id)
            if not pages:
                await interaction.response.send_message("This lookup is too old. Run /monster again.", ephemeral=True)
                return
            if OWNER_ONLY and owner and interaction.user.id != owner:
                await interaction.response.send_message("Only the person who ran the command can switch tabs.", ephemeral=True)
                return
            if key not in pages:
                await interaction.response.send_message("That tab isn't available for this monster.", ephemeral=True)
                return
            await interaction.response.edit_message(embed=pages[key], view=MonsterView(list(pages), key))
        return callback
