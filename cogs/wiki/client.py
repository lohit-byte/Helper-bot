from __future__ import annotations

import asyncio
import json
import shutil
from urllib.parse import urlencode
import aiohttp
from .parser import parse_monster

USER_AGENTS = [
    "Mozilla/5.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
]
TIMEOUT = aiohttp.ClientTimeout(total=20)
CURL = shutil.which("curl")

class WikiClient:
    def __init__(self, base_url="https://monsterlegends.fandom.com"):
        self.api_url = f"{base_url}/api.php"
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENTS[0]})
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _curl_get(self, params: dict) -> tuple[int, str]:
        url = f"{self.api_url}?{urlencode(params)}"
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s", "-m", "20", "-A", USER_AGENTS[0], "-w", "\n%{http_code}", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await proc.communicate()
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise
        body, _, code = out.decode("utf-8", "replace").rpartition("\n")
        return (int(code) if code.strip().isdigit() else 0), body

    async def api(self, params: dict) -> dict:
        params = {**params, "format": "json"}
        status, body = 0, ""
        last_exception = None
        
        if CURL:
            try:
                status, body = await self._curl_get(params)
            except Exception as e:
                last_exception = e
                print(f"[wiki] curl failed: {e!r}")

        if status != 200:
            session = await self._get_session()
            for ua in USER_AGENTS:
                try:
                    async with session.get(self.api_url, params=params, headers={"User-Agent": ua}, timeout=TIMEOUT) as r:
                        status, body = r.status, await r.text()
                except Exception as e:
                    last_exception = e
                    print(f"[wiki] aiohttp failed: {e!r}")
                    continue
                if status == 200: break

        if status != 200:
            if last_exception:
                raise last_exception
            raise RuntimeError(f"Wiki returned HTTP {status}: {body[:200]!r}")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"Wiki returned non-JSON: {body[:200]!r}")

    async def search_titles(self, query: str, limit: int = 10) -> list[str]:
        data = await self.api({"action": "opensearch", "search": query, "limit": limit, "namespace": 0})
        return data[1] if len(data) > 1 else []

    async def get_summary(self, title: str) -> dict | None:
        data = await self.api({"action": "query", "titles": title, "redirects": 1, "prop": "pageimages", "piprop": "original"})
        pages = data.get("query", {}).get("pages", {})
        page = next(iter(pages.values()), None)
        if not page or "missing" in page: return None
        return {"title": page.get("title", title), "image": page.get("original", {}).get("source")}

    async def get_page_data(self, title: str) -> dict:
        data = await self.api({"action": "parse", "page": title, "redirects": 1, "prop": "text|categories", "disableeditsection": 1})
        parsed = data.get("parse", {})
        html = parsed.get("text", {}).get("*", "")
        cats = [c.get("*", "") for c in parsed.get("categories", []) if "hidden" not in c]
        return await asyncio.to_thread(parse_monster, html, cats)