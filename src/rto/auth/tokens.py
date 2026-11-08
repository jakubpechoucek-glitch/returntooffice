"""Bearer-token validation for API clients (Entra ID access tokens, RS256, JWKS)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx
import jwt

from rto.config import get_settings


class TokenError(Exception):
    pass


@lru_cache
def _metadata() -> dict[str, Any]:
    s = get_settings()
    resp = httpx.get(s.oidc_metadata_url, timeout=10)
    resp.raise_for_status()
    return resp.json()


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(_metadata()["jwks_uri"], cache_keys=True, lifespan=3600)


def validate_bearer(token: str) -> dict[str, Any]:
    s = get_settings()
    if not s.api_audience:
        raise TokenError("Bearer tokens are not enabled (RTO_API_AUDIENCE not set)")
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=s.api_audience,
            issuer=_metadata()["issuer"],
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except (jwt.PyJWTError, httpx.HTTPError, KeyError) as exc:
        raise TokenError(str(exc)) from exc
