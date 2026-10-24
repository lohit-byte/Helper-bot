import aiohttp
import re
import io
import config
from PIL import Image

async def process_race_image(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        
        # FIX 1: Crop adjusted to 0.55 to handle wider screens (like iPads)
        img = img.crop((int(w * 0.55), 0, w, h))
        
        buf = io.BytesIO()
        img.convert('RGB').save(buf, format='JPEG', quality=90)
        
        payload = {
            'language': 'eng',
            'isOverlayRequired': 'true',
            'isTable': 'false',
            'OCREngine': '2'
        }
        headers = {'apikey': config.OCR_API_KEY}
        
        form = aiohttp.FormData()
        form.add_field('file', buf.getvalue(), filename='scan.jpg', content_type='image/jpeg')
        for k, v in payload.items():
            form.add_field(k, v)

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
            async with session.post('https://api.ocr.space/parse/image', data=form, headers=headers) as resp:
                data = await resp.json(content_type=None)

        if data.get("IsErroredOnProcessing"):
            print(f"[OCR Error] {data.get('ErrorMessage')}")
            return None

        res = (data.get("ParsedResults") or [{}])[0]
        lines = (res.get("TextOverlay") or {}).get("Lines") or []
        
        if not lines:
            return None

        # FIX 2: Fuzzy Header Matching (handles OCR reading 'One' as '0ne')
        headers_y = {}
        for ln in lines:
            text = ln.get("LineText", "").lower()
            if "for all" in text or "one for" in text: headers_y["ofa"] = ln.get("MinTop")
            elif "your part" in text or "do your" in text: headers_y["dyp"] = ln.get("MinTop")
            elif "joint" in text or "effort" in text: headers_y["je"] = ln.get("MinTop")

        if len(headers_y) != 3:
            print("[OCR] Could not find all 3 headers.")
            return None

        sorted_h = sorted(headers_y.items(), key=lambda x: x[1])

        # FIX 3: Fraction Blocking (prevents two headers from grabbing the same fraction)
        # Handles O/13 instead of 0/13
        FRAC = re.compile(r'([\dOo]+(?:[.,]\d+)?(?:\s*[KMB]\b)?)\s*[/|\\]\s*(\d+(?:[.,]\d+)?(?:\s*[KMB]\b)?)', re.IGNORECASE)
        bounds = [y for _, y in sorted_h] + [10**9]
        targets = {}
        
        for i, (key, h_y) in enumerate(sorted_h):
            cands = []
            for ln in lines:
                top = ln.get("MinTop", 0)
                # Only look for fractions vertically between this header and the next
                if h_y <= top < bounds[i + 1]:
                    for m in FRAC.finditer(ln.get("LineText", "")):
                        cands.append((top, m.group(2).replace(',', '.').upper()))
            if cands:
                targets[key] = min(cands)[1] # Get the top-most fraction in this block

        # Helper to get text between two Y-coordinates
        def get_task_text(start_y, end_y=None):
            text = ""
            for ln in lines:
                if start_y <= ln.get("MinTop", 0) and (end_y is None or ln.get("MinTop", 0) < end_y):
                    text += ln.get("LineText", "") + " "
            return text.lower()

        y_ofa = headers_y["ofa"]
        y_dyp = headers_y["dyp"]
        y_je = headers_y["je"]

        ofa_text = get_task_text(y_ofa, y_dyp)
        dyp_text = get_task_text(y_dyp, y_je)
        je_text = get_task_text(y_je)

        def extract_task(text_block, default_task):
            if "collect" in text_block and "gold" in text_block: return "Collect Gold"
            if "collect" in text_block and "food" in text_block: return "Collect Food"
            if "hatch" in text_block: return "Hatch Monsters"
            if "breed" in text_block: return "Breed Monsters"
            if "rank up" in text_block: return "Rank Up Monsters"
            if "level up" in text_block and "relic" in text_block: 
                color = "Gold" if "gold relic" in text_block else ("Silver" if "silver relic" in text_block else "")
                return f"Level Up {color} Relics"
            
            # FIX 4: Extract Rune Levels (e.g., "Level 3 Rune")
            if "craft" in text_block:
                lv = re.search(r'level\s*(\d+)\s*rune', text_block)
                return f"Craft Level {lv.group(1)} Runes" if lv else "Craft Runes/Relics"
                
            if "win" in text_block: return "Win Battles"
            if "feed" in text_block: return "Feed Food"
            return default_task

        def norm(t):
            return re.sub(r'(\d)\s*([KMB])$', r'\1 \2', t)

        return {
            "ofa": {"target": norm(targets.get("ofa", "Unknown")), "task": extract_task(ofa_text, "Race Task")},
            "dyp": {"target": int(float(re.sub(r'[^\d.]', '', targets.get("dyp", "0")))), "task": extract_task(dyp_text, "Race Task")},
            "je":  {"target": norm(targets.get("je", "Unknown")), "task": extract_task(je_text, "Race Task")}
        }

    except Exception as e:
        print(f"[OCR Error] {e}")
        return None
