import aiohttp
import re
import io
import config
from PIL import Image

def clean_val(v_str):
    """Helper to extract just the number from a string like '4.84 M' or '484M'."""
    try:
        return float(re.sub(r'[^\d.]', '', v_str))
    except ValueError:
        return 0.0

async def process_race_image(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        
        img = img.crop((int(w * 0.55), 0, w, h))
        img = img.convert('RGB')
        
        buf = io.BytesIO()
        for quality in (90, 80, 70, 60, 50):
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=quality)
            if buf.tell() < 1_000_000:
                break
                
        payload = {'language': 'eng', 'isOverlayRequired': 'true', 'isTable': 'false', 'OCREngine': '2'}
        headers = {'apikey': config.OCR_API_KEY}
        
        form = aiohttp.FormData()
        form.add_field('file', buf.getvalue(), filename='scan.jpg', content_type='image/jpeg')
        for k, v in payload.items():
            form.add_field(k, v)

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            async with session.post('https://api.ocr.space/parse/image', data=form, headers=headers) as resp:
                data = await resp.json(content_type=None)

        if data.get("IsErroredOnProcessing"):
            return None

        res = (data.get("ParsedResults") or [{}])[0]
        lines = (res.get("TextOverlay") or {}).get("Lines") or []
        
        if not lines:
            return None

        headers_y = {}
        for ln in lines:
            text = ln.get("LineText", "").lower()
            if "for all" in text or "one for" in text: headers_y["ofa"] = ln.get("MinTop")
            elif "your part" in text or "do your" in text: headers_y["dyp"] = ln.get("MinTop")
            elif "joint" in text or "effort" in text: headers_y["je"] = ln.get("MinTop")

        if len(headers_y) != 3:
            return None

        sorted_h = sorted(headers_y.items(), key=lambda x: x[1])

        FRAC = re.compile(r'([\dOo]+(?:[.,]\d+)?(?:\s*[KMB]\b)?)\s*[/|\\]\s*(\d+(?:[.,]\d+)?(?:\s*[KMB]\b)?)', re.IGNORECASE)
        bounds = [y for _, y in sorted_h] + [10**9]
        
        extracted_data = {}
        
        for i, (key, h_y) in enumerate(sorted_h):
            cands = []
            for ln in lines:
                top = ln.get("MinTop", 0)
                if h_y <= top < bounds[i + 1]:
                    for m in FRAC.finditer(ln.get("LineText", "")):
                        prog = m.group(1).replace('O', '0').replace('o', '0').replace(',', '.').upper()
                        targ = m.group(2).replace(',', '.').upper()
                        cands.append((top, prog, targ))
            if cands:
                best = min(cands)
                extracted_data[key] = {"progress": best[1], "target": best[2]}

        if len(extracted_data) != 3:
            return None

        # Sanity check for missed decimals
        for key in ["ofa", "je"]:
            if key in extracted_data:
                p_str = extracted_data[key]["progress"]
                t_str = extracted_data[key]["target"]
                
                p_val = clean_val(p_str)
                t_val = clean_val(t_str)
                
                # If progress is higher than target, it's almost certainly a missed dot (e.g., 484 > 4.84)
                if p_val > t_val:
                    print(f"[OCR Fix] Corrected {key.upper()} progress from {p_str} to {t_str}", flush=True)
                    extracted_data[key]["progress"] = t_str

        def get_task_text(start_y, end_y=None):
            text = ""
            for ln in lines:
                if start_y <= ln.get("MinTop", 0) and (end_y is None or ln.get("MinTop", 0) < end_y):
                    text += ln.get("LineText", "") + " "
            return text.lower()

        ofa_text = get_task_text(headers_y["ofa"], headers_y["dyp"])
        dyp_text = get_task_text(headers_y["dyp"], headers_y["je"])
        je_text = get_task_text(headers_y["je"])

        def extract_task(text_block, default_task):
            if "collect" in text_block and "gold" in text_block: return "Collect Gold"
            if "collect" in text_block and "food" in text_block: return "Collect Food"
            if "hatch" in text_block: return "Hatch Monsters"
            if "breed" in text_block: return "Breed Monsters"
            if "rank up" in text_block: return "Rank Up Monsters"
            if "level up" in text_block and "relic" in text_block: 
                color = "Gold" if "gold relic" in text_block else ("Silver" if "silver relic" in text_block else "")
                return f"Level Up {color} Relics".replace("  ", " ")
            if "craft" in text_block:
                lv = re.search(r'level\s*(\d+)\s*rune', text_block)
                return f"Craft Level {lv.group(1)} Runes" if lv else "Craft Runes"
            if re.search(r'\bwin\b', text_block): return "Win Battles"
            if "feed" in text_block: return "Feed Food"
            return default_task

        def norm(t):
            return re.sub(r'(\d)\s*([KMB])$', r'\1 \2', t)

        return {
            "ofa": {"progress": norm(extracted_data["ofa"]["progress"]), "target": norm(extracted_data["ofa"]["target"]), "task": extract_task(ofa_text, "Race Task")},
            "dyp": {"progress": int(float(re.sub(r'[^\d.]', '', extracted_data["dyp"]["progress"]))), "target": int(float(re.sub(r'[^\d.]', '', extracted_data["dyp"]["target"]))), "task": extract_task(dyp_text, "Race Task")},
            "je":  {"progress": norm(extracted_data["je"]["progress"]), "target": norm(extracted_data["je"]["target"]), "task": extract_task(je_text, "Race Task")}
        }

    except Exception as e:
        print(f"[OCR Error] {e}", flush=True)
        return None
