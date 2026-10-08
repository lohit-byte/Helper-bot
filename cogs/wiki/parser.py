import re
from bs4 import BeautifulSoup, NavigableString

def clean(text: str) -> str:
    return " ".join(text.split())

ELEMENTS = {"Fire", "Water", "Nature", "Earth", "Thunder", "Metal", "Magic", "Light", "Dark", "Legend"}
RARITIES = {"Common", "Rare", "Epic", "Mythic", "Legendary", "Primal", "Cosmic"}
ROLES = ("Attacker", "Tank", "Control", "Support", "Healer")
UNIT_ORDER = {"d": 4, "h": 3, "m": 2, "s": 1}
IMG_EXT = re.compile(r"\.(png|jpg|jpeg|gif|webp|svg)$", re.I)

def between(text: str, start: str, ends: tuple[str, ...] = ()) -> str:
    i = text.find(start)
    if i < 0: return ""
    i += len(start)
    stops = [j for j in (text.find(e, i) for e in ends) if j >= 0]
    return text[i:min(stops) if stops else None].strip()

def split_times(tokens: list[str]) -> list[str]:
    groups: list[list[str]] = []
    for tok in tokens:
        unit = UNIT_ORDER[tok[-1]]
        if groups and UNIT_ORDER[groups[-1][-1][-1]] > unit:
            groups[-1].append(tok)
        else:
            groups.append([tok])
    return [" ".join(g) for g in groups]

def img_name(img) -> str:
    link = img.find_parent("a")
    name = (link.get("title") if link else "") or img.get("alt") or img.get("data-image-name") or ""
    return clean(IMG_EXT.sub("", name.strip()))

def cell_items(cell) -> list[str]:
    items: list[str] = []
    for node in cell.descendants:
        if isinstance(node, NavigableString): t = clean(str(node))
        elif getattr(node, "name", None) == "img": t = img_name(node)
        else: continue
        if t and (not items or items[-1] != t): items.append(t)
    return items

def col_index(header: list[str], word: str):
    return next((i for i, h in enumerate(header) if word in h), None)

def cell_at(cells: list, header: list[str], idx):
    if idx is None: return None
    i = idx - (len(header) - len(cells))
    return cells[i] if 0 <= i < len(cells) else None

def parse_tables(soup) -> dict:
    out: dict = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2: continue
        header = [" ".join(cell_items(c)).lower() for c in rows[0].find_all(["th", "td"])]
        htxt = " ".join(header)
        body = [r.find_all(["th", "td"]) for r in rows[1:]]

        if "ranks" not in out and all(w in htxt for w in ("power", "life", "speed", "stamina")):
            ranks = []
            for cells in body:
                nums = [t for t in (" ".join(cell_items(c)) for c in cells) if re.fullmatch(r"\d[\d,]*", t)]
                if len(nums) >= 4: ranks.append(tuple(nums[-4:]))
            if ranks: out["ranks"] = ranks
        elif "trait_items" not in out and "trait" in htxt and "gpm" in htxt:
            ti, gi = col_index(header, "trait"), col_index(header, "gpm")
            last_items, last_gpm = [], ""
            for cells in body:
                tc, gc = cell_at(cells, header, ti), cell_at(cells, header, gi)
                if tc is None or gc is None: continue
                items = cell_items(tc)
                if items: last_items = items
                g = " ".join(cell_items(gc))
                if re.fullmatch(r"\d[\d,]*", g): last_gpm = g
            if last_items: out["trait_items"] = last_items
            if last_gpm: out["max_gpm"] = last_gpm
        elif "books" not in out and "book" in htxt and "role" in htxt:
            cells = body[0]
            bi, ri, roi = col_index(header, "book"), col_index(header, "relic"), col_index(header, "role")
            b, r, ro = cell_at(cells, header, bi), cell_at(cells, header, ri), cell_at(cells, header, roi)
            out["books"] = cell_items(b) if b is not None else []
            out["relics"] = cell_items(r) if r is not None else []
            if ro is not None: out["role"] = " ".join(cell_items(ro))
        elif "breed" not in out and "price" in htxt and "hatch" in htxt:
            cells = body[0]
            for key in ("price", "exp", "sell", "breed", "hatch"):
                c = cell_at(cells, header, col_index(header, key))
                if c is not None: out[key] = " ".join(cell_items(c))
    return out

def parse_text_fallback(plain: str, withalt: str) -> dict:
    out: dict = {}
    section = between(plain, "Stats and Information", ("Obtention", "Skills", "Trivia")) or plain
    ranks = re.findall(r"(?:\d{1,2}\s+)?(\d[\d,]{2,})\s+(\d[\d,]{2,})\s+(\d[\d,]{2,})\s+(\d{2,3})(?=\s|$)", section.split("Trait")[0])
    if ranks: out["ranks"] = ranks

    seg = between(section, "GPM", ("Book", "Relic", "Role", "Price"))
    if seg:
        m = re.match(r"(?:\d{1,2}\s+)?(.*?)\s+\d{2,4}(?=\s|$)", seg)
        if m and m.group(1).strip(): out["trait_items"] = [m.group(1).strip()]
        if seg.split()[-1].isdigit(): out["max_gpm"] = seg.split()[-1]

    m = re.search(r"(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+((?:\d+[dhms]\s*)+)(?=\s|$|[^\w])", section)
    if m:
        out["price"], out["exp"], out["sell"] = m.group(1), m.group(2), m.group(3)
        times = split_times(re.findall(r"\d+[dhms]", m.group(4)))
        if times: out["breed"] = times[0]
        if len(times) > 1: out["hatch"] = times[1]

    role_seg = between(withalt, "Role", ("Price", "Obtention", "Skills"))
    m = re.search(r"\b(" + "|".join(ROLES) + r")\b", role_seg)
    if m: out["role"] = m.group(1)
    return out

def parse_monster(html: str, categories: list[str]) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style"]): tag.decompose()

    description = ""
    for p in soup.find_all("p"):
        t = clean(p.get_text(" ", strip=True))
        if len(t) >= 40:
            description = t
            break

    data = parse_tables(soup)
    plain = clean(soup.get_text(" ", strip=True)).replace("{", "").replace("}", "")
    for img in soup.find_all("img"):
        name = img_name(img)
        img.replace_with(f" {name} " if name else " ")
    withalt = clean(soup.get_text(" ", strip=True)).replace("{", "").replace("}", "")

    if not description and "Stats and Information" in plain:
        description = plain.split("Stats and Information")[0][-300:].strip()

    wanted = ("ranks", "trait_items", "role", "breed", "hatch", "sell")
    if any(k not in data for k in wanted):
        for k, v in parse_text_fallback(plain, withalt).items():
            data.setdefault(k, v)

    combos: list[str] = []
    obtain = ""
    ob = between(withalt, "Obtention", ("Skills", "Trivia"))
    if ob:
        combos = [clean(c) for c in re.findall(r"(.+?\(\s*[\d.]+\s*%\s*chance\s*\))", ob)]
        if not combos: obtain = ob[:300]

    cats = [c.replace("_", " ") for c in categories]
    cat_words = {w for c in cats for w in re.split(r"\s+", c)}
    return {
        "description": description, "ranks": data.get("ranks", []),
        "trait": ", ".join(data.get("trait_items", [])), "max_gpm": data.get("max_gpm", ""),
        "role": data.get("role", ""), "books": data.get("books", []), "relics": data.get("relics", []),
        "price": data.get("price", ""), "exp": data.get("exp", ""), "sell": data.get("sell", ""),
        "breed": data.get("breed", ""), "hatch": data.get("hatch", ""),
        "combos": combos, "obtain": obtain,
        "rarity": sorted(cat_words & RARITIES), "elements": sorted(cat_words & ELEMENTS),
        "categories": cats,
    }