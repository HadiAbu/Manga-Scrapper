import httpx
from fastapi import APIRouter, Depends, HTTPException
from opensearchpy.exceptions import NotFoundError
from .auth import get_current_user
from .search import INDEX, _get_client

MANGADEX_API = "https://api.mangadex.org"
COMICK_API = "https://api.comick.io"

_MD = "md"  # MangaDex source prefix
_CK = "ck"  # Comick source prefix

router = APIRouter()


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _mangadex_error(e: httpx.HTTPStatusError) -> HTTPException:
    if e.response.status_code == 404:
        return HTTPException(status_code=404, detail="Chapter not available on MangaDex (may have been removed)")
    if e.response.status_code == 429:
        return HTTPException(status_code=429, detail="MangaDex rate limit reached — please wait a moment")
    return HTTPException(status_code=502, detail=f"MangaDex returned {e.response.status_code}")


async def _mangadex_get(http: httpx.AsyncClient, url: str, **kwargs) -> dict:
    try:
        resp = await http.get(url, timeout=10, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        raise _mangadex_error(e)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach MangaDex: {e}")


async def _comick_get(http: httpx.AsyncClient, url: str, **kwargs) -> dict | list:
    try:
        resp = await http.get(url, timeout=10, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Not found on Comick")
        raise HTTPException(status_code=502, detail=f"Comick returned {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Comick: {e}")


# ── Source lookup helpers ─────────────────────────────────────────────────────

async def _find_mangadex_id(mal_id: int, title: str, title_english: str) -> str:
    """Search MangaDex by title, preferring entries whose links.mal matches mal_id."""
    async with httpx.AsyncClient() as http:
        for search_term in filter(None, [title, title_english]):
            data = await _mangadex_get(
                http, f"{MANGADEX_API}/manga",
                params={"title": search_term, "limit": 10}
            )
            results = data.get("data", [])
            if not results:
                continue
            for result in results:
                links = (result.get("attributes") or {}).get("links") or {}
                if str(links.get("mal", "")) == str(mal_id):
                    return result["id"]
            return results[0]["id"]
    raise HTTPException(status_code=404, detail=f"'{title}' not found on MangaDex")


async def _find_comick_hid(mal_id: int, title: str, title_english: str) -> str:
    """Search Comick by title, preferring entries whose links.mal matches mal_id."""
    async with httpx.AsyncClient() as http:
        for search_term in filter(None, [title, title_english]):
            data = await _comick_get(
                http, f"{COMICK_API}/v1.0/search",
                params={"q": search_term, "limit": 10, "t": "1"}
            )
            results = data if isinstance(data, list) else data.get("data", [])
            if not results:
                continue
            for result in results:
                links = result.get("links") or {}
                if str(links.get("mal", "")) == str(mal_id):
                    return result["hid"]
            return results[0]["hid"]
    raise HTTPException(status_code=404, detail=f"'{title}' not found on Comick")


async def _find_comick_chapter_hid(manga_hid: str, chap_num: str) -> str:
    """Paginate Comick chapters to find the HID matching chap_num."""
    limit = 300
    # Estimate starting page assuming ascending order (oldest first).
    # For chapter 1183 this jumps straight to page ~4 instead of scanning from page 1.
    try:
        estimated_page = max(1, int((float(chap_num) - 1) / limit) + 1)
    except (ValueError, TypeError):
        estimated_page = 1

    async with httpx.AsyncClient() as http:
        seen: set[int] = set()
        # Try the estimated page first, then sequential pages from 1 as fallback
        # (covers both ascending and descending chapter ordering from Comick).
        for page in [estimated_page, *range(1, 20)]:
            if page in seen:
                continue
            seen.add(page)

            data = await _comick_get(
                http, f"{COMICK_API}/comic/{manga_hid}/chapters",
                params={"lang": "en", "limit": limit, "page": page},
            )
            chapters = data.get("chapters", []) if isinstance(data, dict) else []
            if not chapters:
                break

            for ch in chapters:
                # Normalise "1183" == "1183.0" mismatches
                try:
                    match = float(ch.get("chap") or "nan") == float(chap_num)
                except ValueError:
                    match = str(ch.get("chap", "")) == chap_num
                if match:
                    return ch["hid"]

    raise HTTPException(status_code=404, detail=f"Chapter {chap_num} not found on Comick")


# ── Chapter fetchers ──────────────────────────────────────────────────────────

async def _chapters_from_mangadex(mal_id: int, title: str, title_english: str) -> list[dict]:
    mangadex_id = await _find_mangadex_id(mal_id, title, title_english)
    async with httpx.AsyncClient() as http:
        data = await _mangadex_get(
            http,
            f"{MANGADEX_API}/manga/{mangadex_id}/feed",
            params={
                "translatedLanguage[]": "en",
                "order[volume]": "asc",
                "order[chapter]": "asc",
                "limit": 500,
            },
        )
    raw = data.get("data", [])
    if not raw:
        raise HTTPException(status_code=404, detail="No English chapters found on MangaDex")

    # Embed mal_id and chapter number into the ID so the pages endpoint
    # can fall back to Comick if MangaDex's at-home server returns 404.
    # Format: md:{uuid}:{mal_id}:{chap_num}
    return [
        {
            "id": f"{_MD}:{ch['id']}:{mal_id}:{ch['attributes'].get('chapter') or ''}",
            "volume": ch["attributes"].get("volume"),
            "chapter": ch["attributes"].get("chapter"),
            "title": ch["attributes"].get("title"),
            "pages": ch["attributes"].get("pages", 0),
        }
        for ch in raw
    ]


async def _chapters_from_comick(mal_id: int, title: str, title_english: str) -> list[dict]:
    hid = await _find_comick_hid(mal_id, title, title_english)
    async with httpx.AsyncClient() as http:
        data = await _comick_get(
            http, f"{COMICK_API}/comic/{hid}/chapters",
            params={"lang": "en", "limit": 300, "page": 1}
        )
    raw = data.get("chapters", []) if isinstance(data, dict) else []
    if not raw:
        raise HTTPException(status_code=404, detail="No English chapters found on Comick")
    return [
        {
            "id": f"{_CK}:{ch['hid']}",
            "volume": ch.get("vol"),
            "chapter": ch.get("chap"),
            "title": ch.get("title"),
            "pages": ch.get("page_count", 0),
        }
        for ch in raw
    ]


# ── Page fetchers ─────────────────────────────────────────────────────────────

async def _pages_from_mangadex(chapter_id: str) -> dict:
    async with httpx.AsyncClient() as http:
        data = await _mangadex_get(http, f"{MANGADEX_API}/at-home/server/{chapter_id}")
    ch = data.get("chapter", {})
    base = data.get("baseUrl", "")
    hash_ = ch.get("hash", "")
    data_saver = ch.get("dataSaver", [])
    if not hash_ or not data_saver:
        raise HTTPException(status_code=404, detail="No pages found for this chapter")
    return {
        "chapter_id": f"{_MD}:{chapter_id}",
        "pages": [f"{base}/data-saver/{hash_}/{p}" for p in data_saver],
        "total": len(data_saver),
    }


async def _pages_from_comick(chapter_hid: str) -> dict:
    async with httpx.AsyncClient() as http:
        data = await _comick_get(http, f"{COMICK_API}/chapter/{chapter_hid}")
    chapter = data.get("chapter", {}) if isinstance(data, dict) else {}
    images = chapter.get("images") or data.get("images", [])
    if not images:
        raise HTTPException(status_code=404, detail="No pages found for this chapter on Comick")
    pages = [img["url"] for img in images if img.get("url")]
    if not pages:
        raise HTTPException(status_code=404, detail="No page URLs found for this chapter")
    return {"chapter_id": f"{_CK}:{chapter_hid}", "pages": pages, "total": len(pages)}


# ── ID parsing ────────────────────────────────────────────────────────────────

def _parse_chapter_id(chapter_id: str) -> tuple[str, str, int | None, str | None]:
    """
    Supported formats:
      md:{uuid}:{mal_id}:{chap}  — MangaDex chapter with Comick fallback context
      ck:{hid}                   — Comick chapter (already the fallback source)
      {bare-uuid}                — legacy MangaDex UUID without fallback context
    """
    parts = chapter_id.split(":", 3)
    if len(parts) >= 2 and parts[0] in (_MD, _CK):
        source = parts[0]
        raw_id = parts[1]
        mal_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        chap_num = parts[3] if len(parts) > 3 and parts[3] else None
        return source, raw_id, mal_id, chap_num
    return _MD, chapter_id, None, None  # legacy bare UUID → assume MangaDex


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/manga/{mal_id}/chapters")
async def get_chapters(mal_id: int, _user: dict = Depends(get_current_user)):
    client = _get_client()
    try:
        doc = client.get(index=INDEX, id=mal_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Manga not found in index")

    src = doc["_source"]
    title = src.get("title", "")
    title_english = src.get("title_english", "")

    # Primary: MangaDex
    try:
        chapters = await _chapters_from_mangadex(mal_id, title, title_english)
        return {"manga_title": title, "source": "mangadex", "chapters": chapters}
    except HTTPException as exc:
        if exc.status_code != 404:
            raise  # propagate 429, 502, etc.

    # Fallback: Comick
    try:
        chapters = await _chapters_from_comick(mal_id, title, title_english)
        return {"manga_title": title, "source": "comick", "chapters": chapters}
    except HTTPException:
        raise HTTPException(
            status_code=404,
            detail=f"No English chapters found for '{title}' on MangaDex or Comick"
        )


@router.get("/chapters/{chapter_id}/pages")
async def get_pages(chapter_id: str, _user: dict = Depends(get_current_user)):
    source, raw_id, mal_id, chap_num = _parse_chapter_id(chapter_id)

    # Comick chapters go directly — no further fallback needed
    if source == _CK:
        return await _pages_from_comick(raw_id)

    # Primary: MangaDex at-home server
    try:
        return await _pages_from_mangadex(raw_id)
    except HTTPException as exc:
        # Only attempt Comick fallback on 404; propagate 429/502 immediately.
        # Also skip if the chapter ID doesn't carry fallback context (legacy format).
        if exc.status_code != 404 or mal_id is None or chap_num is None:
            raise

    # Fallback: find the same chapter number on Comick
    os_client = _get_client()
    try:
        doc = os_client.get(index=INDEX, id=mal_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Chapter removed from MangaDex; manga not found for Comick fallback")

    src = doc["_source"]
    title = src.get("title", "")
    title_english = src.get("title_english", "")

    try:
        comick_manga_hid = await _find_comick_hid(mal_id, title, title_english)
        comick_chapter_hid = await _find_comick_chapter_hid(comick_manga_hid, chap_num)
        return await _pages_from_comick(comick_chapter_hid)
    except HTTPException:
        raise HTTPException(
            status_code=404,
            detail=f"Chapter {chap_num} not available on MangaDex or Comick"
        )
