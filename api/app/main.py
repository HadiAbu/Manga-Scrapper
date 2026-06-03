from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .search import router as search_router, init_client
from .reader import router as reader_router
from .auth import fetch_jwks


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_client()
    try:
        await fetch_jwks()
    except Exception:
        # Keycloak may still be warming up; JWKS will be fetched lazily on first request
        pass
    yield


app = FastAPI(title="Manga Search API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router, prefix="/api")
app.include_router(reader_router, prefix="/api")
