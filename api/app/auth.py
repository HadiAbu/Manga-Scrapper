import os
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080/auth")
KEYCLOAK_PUBLIC_URL = os.getenv("KEYCLOAK_PUBLIC_URL", "http://localhost/auth")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "manga")

_jwks_cache: Optional[dict] = None

security = HTTPBearer(auto_error=False)


def _jwks_url() -> str:
    return f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"


def _expected_issuer() -> str:
    return f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}"


async def fetch_jwks(force_refresh: bool = False) -> dict:
    global _jwks_cache
    if _jwks_cache is not None and not force_refresh:
        return _jwks_cache
    async with httpx.AsyncClient() as client:
        resp = await client.get(_jwks_url(), timeout=10)
        resp.raise_for_status()
        _jwks_cache = resp.json()
    return _jwks_cache


def _find_key(jwks: dict, kid: Optional[str]) -> Optional[dict]:
    return next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        jwks = await fetch_jwks()
        key = _find_key(jwks, kid)
        if key is None:
            # Keycloak may have rotated keys — refresh cache once
            jwks = await fetch_jwks(force_refresh=True)
            key = _find_key(jwks, kid)
        if key is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signing key not found")

        payload = jwt.decode(token, key, algorithms=["RS256"], options={"verify_aud": False})

        if payload.get("iss") != _expected_issuer():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token issuer")

        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
