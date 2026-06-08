import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
import httpx
from opensearchpy import OpenSearch
from opensearchpy.exceptions import NotFoundError

ALLOWED_IMAGE_HOST = "cdn.myanimelist.net"

INDEX = "manga"
_client: OpenSearch = None


def init_client():
    global _client
    url = os.getenv("OPENSEARCH_URL", "http://opensearch:9200")
    _client = OpenSearch(hosts=[url])


def _get_client() -> OpenSearch:
    if _client is None:
        raise HTTPException(status_code=503, detail="Search client not initialized")
    return _client


router = APIRouter()


@router.get("/search")
def search(
    q: str = "",
    page: int = 1,
    limit: int = 24,
    genre: str = "",
):
    client = _get_client()
    offset = (page - 1) * limit

    must = []
    if q.strip():
        must.append({
            "multi_match": {
                "query": q,
                "fields": ["title^3", "title_english^2", "synopsis"],
                "type": "best_fields",
                "fuzziness": "AUTO",
            }
        })
    if genre:
        must.append({"term": {"genres": genre}})

    query = {"match_all": {}} if not must else {"bool": {"must": must}}
    sort = [{"score": {"order": "desc", "missing": "_last"}}] if not q.strip() else ["_score"]

    body = {"query": query, "sort": sort, "from": offset, "size": limit}

    try:
        res = client.search(index=INDEX, body=body)
    except NotFoundError:
        return {"total": 0, "page": page, "limit": limit, "results": []}

    hits = res["hits"]
    return {
        "total": hits["total"]["value"],
        "page": page,
        "limit": limit,
        "results": [h["_source"] for h in hits["hits"]],
    }


@router.get("/genres")
def list_genres():
    client = _get_client()
    body = {
        "size": 0,
        "aggs": {"genres": {"terms": {"field": "genres", "size": 100}}},
    }
    try:
        res = client.search(index=INDEX, body=body)
    except NotFoundError:
        return []
    return [b["key"] for b in res["aggregations"]["genres"]["buckets"]]


@router.get("/image-proxy")
async def image_proxy(url: str):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.netloc != ALLOWED_IMAGE_HOST:
        raise HTTPException(status_code=400, detail="Image host not allowed")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10, follow_redirects=True)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Upstream returned {e.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not reach image CDN")
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get("/health")
def health():
    client = _get_client()
    try:
        client.ping()
    except Exception as e:
        return {"status": "error", "detail": str(e)}

    try:
        info = client.cat.indices(index=INDEX, h="docs.count", format="json")
        count = int(info[0]["docs.count"]) if info else 0
    except Exception:
        count = 0

    return {"status": "ok", "manga_count": count}


@router.get("/manga/{mal_id}")
def get_manga(mal_id: int):
    client = _get_client()
    try:
        res = client.get(index=INDEX, id=mal_id)
        return res["_source"]
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Manga not found")
