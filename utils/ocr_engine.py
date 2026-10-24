import aiohttp
import re
import io
import config
from PIL import Image

async def process_race_image(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        
        # Crop the right side (Quest Panel)
        img = img.crop((int(w * 0.65), 0, w, h))
        
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

        headers_y = {}
        for ln in lines:
            text = ln.get("LineText", "").lower()
            if "one for all" in text: headers_y["ofa"] = ln.get("MinTop")
            elif "do your part" in text: headers_y["dyp"] = ln.get("MinTop")
            elif "joint effort" in text: headers_y["je"] = ln.get("MinTop")

        if len(headers_y) != 3:
            return None

        sorted_h = sorted(headers_y.items(), key=lambda x: x[1])
        FRAC = re.compile(r'(\d+(?:[.,]\d+)?(?:\s*[KMB]\b)?)\s*[/|\\]\s*(\d+(?:[.,]\d+)?(?:\s*[KMB]\b)?)', re.IGNORECASE)
        
        fractions = []
        for ln in lines:
            for m in FRAC.finditer(ln.get("LineText", "")):
                fractions.append({
                    "y": ln.get("MinTop"),
                    "target": m.group(2).replace(',', '.').upper()
                })

        targets = {}
        for key, h_y in sorted_h:
            if not fractions: break
            best_match = min(fractions, key=lambda f: abs(f["y"] - h_y))
            targets[key] = best_match["target"]

        full_text = res.get("ParsedText", "").lower()
        
        def extract_task(default_task):
            if "collect" in full_text and "gold" in full_text: return "Collect Gold"
            if "collect" in full_text and "food" in full_text: return "Collect Food"
            if "hatch" in full_text: return "Hatch Monsters"
            if "breed" in full_text: return "Breed Monsters"
            if "rank up" in full_text: return "Rank Up Monsters"
            if "level up" in full_text and "relic" in full_text: 
                color = "Gold" if "gold relic" in full_text else ("Silver" if "silver relic" in full_text else "")
                return f"Level up {color} Relics"
            if "craft" in full_text: return "Craft Runes/Relics"
            if "win" in full_text: return "Win Battles"
            if "feed" in full_text: return "Feed Food"
            return default_task

        def norm(t):
            return re.sub(r'(\d)\s*([KMB])$', r'\1 \2', t)

        return {
            "ofa": {"target": norm(targets.get("ofa", "Unknown")), "task": extract_task("Race Task")},
            "dyp": {"target": int(float(re.sub(r'[^\d.]', '', targets.get("dyp", "0")))), "task": extract_task("Race Task")},
            "je":  {"target": norm(targets.get("je", "Unknown")), "task": extract_task("Race Task")}
        }

    except Exception as e:
        print(f"[OCR Error] {e}")
        return None
