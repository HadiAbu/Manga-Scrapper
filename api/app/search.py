import os
from fastapi import APIRouter, Depends, HTTPException
from opensearchpy import OpenSearch
from opensearchpy.exceptions import NotFoundError
from .auth import get_current_user

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
    _user: dict = Depends(get_current_user),
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
def list_genres(_user: dict = Depends(get_current_user)):
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
def get_manga(mal_id: int, _user: dict = Depends(get_current_user)):
    client = _get_client()
    try:
        res = client.get(index=INDEX, id=mal_id)
        return res["_source"]
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Manga not found")
