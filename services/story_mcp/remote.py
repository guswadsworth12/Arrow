"""OAuth-protected Streamable HTTP resource server. The identity provider owns login/PKCE."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from urllib.parse import urlsplit

import anyio
import httpx
import jwt
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from services.story_service.client import Client
from .server import create_server

SCOPES = ("arrow:read", "arrow:edit", "arrow:apply")
ROLE_SCOPES = {"arrow-reader": {"arrow:read"}, "arrow-editor": {"arrow:read", "arrow:edit"}, "arrow-committer": set(SCOPES)}


def http_url(value: str, label: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError(f"{label} is not a valid URL") from None
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or "*" in value or any(ord(char) <= 32 for char in value)
            or (port is not None and not 1 <= port <= 65535)
            or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
        raise ValueError(f"{label} requires HTTPS (HTTP is allowed only on loopback), without credentials/query/fragment")
    return str(AnyHttpUrl(value))


@dataclass(frozen=True)
class RemoteConfig:
    public_url: str
    issuer_url: str
    jwks_url: str
    allowed_origins: tuple[str, ...] = ()
    keycloak_roles: bool = False

    def __post_init__(self):
        for field in ("public_url", "issuer_url", "jwks_url"):
            object.__setattr__(self, field, http_url(getattr(self, field), field))
        if urlsplit(self.public_url).path != "/mcp":
            raise ValueError("ARROW_MCP_PUBLIC_URL must end in /mcp, without a trailing slash")
        for value in self.allowed_origins:
            parsed = urlsplit(http_url(value, "allowed origin"))
            if parsed.path != "/" or value.rstrip("/") != f"{parsed.scheme}://{parsed.netloc}":
                raise ValueError("Allowed MCP origins must be exact origins without paths")

    @classmethod
    def environment(cls):
        fields = {field: os.environ.get(name, "") for field, name in (
            ("public_url", "ARROW_MCP_PUBLIC_URL"), ("issuer_url", "ARROW_MCP_ISSUER_URL"), ("jwks_url", "ARROW_MCP_JWKS_URL"))}
        if not all(fields.values()):
            raise ValueError("Remote MCP requires ARROW_MCP_PUBLIC_URL, ARROW_MCP_ISSUER_URL and ARROW_MCP_JWKS_URL")
        try:
            origins = json.loads(os.environ.get("ARROW_MCP_ALLOWED_ORIGINS", "[]"))
        except ValueError:
            raise ValueError("ARROW_MCP_ALLOWED_ORIGINS must be a JSON array of exact origins") from None
        if not isinstance(origins, list) or not all(isinstance(value, str) for value in origins):
            raise ValueError("ARROW_MCP_ALLOWED_ORIGINS must be a JSON array of exact origins")
        mode = os.environ.get("ARROW_MCP_KEYCLOAK_ROLES", "false")
        if mode not in ("true", "false"):
            raise ValueError("ARROW_MCP_KEYCLOAK_ROLES must be true or false")
        return cls(**fields, allowed_origins=tuple(value.rstrip("/") for value in origins), keycloak_roles=mode == "true")


class JWTVerifier:
    """Validate signatures, exact issuer/resource audience, expiry and permission claims.

    JWKS URLs come only from operator configuration, never untrusted token headers.
    A bounded key cache supports rotation without refetching for every invalid token.
    """
    def __init__(self, config: RemoteConfig):
        self.config = config
        self.keys = {}
        self.expires = 0.0
        self.next_fetch = 0.0
        self.lock = anyio.Lock()

    async def _key(self, kid: str):
        now = time.monotonic()
        if kid in self.keys and now < self.expires:
            return self.keys[kid]
        async with self.lock:
            now = time.monotonic()
            if kid in self.keys and now < self.expires:
                return self.keys[kid]
            if now >= self.next_fetch:
                self.next_fetch = now + 10
                try:
                    async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                        async with client.stream("GET", self.config.jwks_url) as response:
                            if response.status_code != 200:
                                return None
                            raw = bytearray()
                            async for chunk in response.aiter_bytes():
                                raw.extend(chunk)
                                if len(raw) > 256 * 1024:
                                    return None
                    data = json.loads(raw)
                    if not isinstance(data, dict) or not isinstance(data.get("keys"), list) or len(data["keys"]) > 64:
                        return None
                    keys = {}
                    for value in data["keys"]:
                        if (not isinstance(value, dict) or value.get("kty") != "RSA" or value.get("use", "sig") != "sig"
                                or value.get("alg", "RS256") != "RS256" or not isinstance(value.get("kid"), str)):
                            continue
                        if value["kid"] in keys:
                            return None
                        key = jwt.PyJWK.from_dict(value, algorithm="RS256").key
                        if key.key_size < 2048:
                            return None
                        keys[value["kid"]] = key
                    self.keys, self.expires = keys, time.monotonic() + 300
                except (httpx.HTTPError, jwt.PyJWTError, ValueError, TypeError, KeyError):
                    return None
            return self.keys.get(kid) if time.monotonic() < self.expires else None

    async def verify_token(self, token: str) -> AccessToken | None:
        if len(token) > 16 * 1024:
            return None
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not 1 <= len(header["kid"]) <= 128:
                return None
            key = await self._key(header["kid"])
            if key is None:
                return None
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.config.public_url,
                                issuer=self.config.issuer_url, leeway=5,
                                options={"require": ["exp", "iat", "sub", "iss", "aud"]})
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                return None
            scope = claims.get("scope", "")
            if not isinstance(scope, str):
                return None
            scopes = set(scope.split()) & set(SCOPES)
            if self.config.keycloak_roles:
                realm = claims.get("realm_access", {})
                roles = realm.get("roles", []) if isinstance(realm, dict) else []
                if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
                    return None
                allowed = set().union(*(ROLE_SCOPES.get(role, set()) for role in roles))
                scopes &= allowed
            client_id = claims.get("azp", claims.get("client_id", claims["sub"]))
            if not isinstance(client_id, str) or not client_id:
                return None
            return AccessToken(token=token, client_id=client_id, scopes=sorted(scopes), expires_at=int(claims["exp"]),
                               subject=claims["sub"], resource=self.config.public_url)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError):
            return None


class PrivateResponses:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send_private(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"cache-control"] + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]
            await send(message)
        return await self.app(scope, receive, send_private)


def create_remote_app(*, client: Client | None = None, config: RemoteConfig | None = None, allow_apply: bool = False):
    config = config or RemoteConfig.environment()
    client = client or Client(os.environ.get("ARROW_STORY_URL", "http://127.0.0.1:8765"))
    auth = AuthSettings(issuer_url=AnyHttpUrl(config.issuer_url), resource_server_url=AnyHttpUrl(config.public_url),
                        required_scopes=["arrow:read"], validate_token_resource=True)
    parsed = urlsplit(config.public_url)
    server = create_server(client, allow_apply=allow_apply, auth=auth, token_verifier=JWTVerifier(config),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[parsed.netloc], allowed_origins=list(config.allowed_origins)))
    app = server.streamable_http_app()
    # Advertise optional write/commit scopes without requiring them for read calls.
    supported = list(SCOPES if allow_apply else SCOPES[:2])
    metadata = create_protected_resource_routes(resource_url=AnyHttpUrl(config.public_url),
        authorization_servers=[AnyHttpUrl(config.issuer_url)], scopes_supported=supported, resource_name="Arrow story workspace")
    app.router.routes = [route for route in app.routes if not route.path.startswith("/.well-known/oauth-protected-resource")] + metadata
    async def health(request):
        return JSONResponse({"status": "ok", "transport": "streamable-http"})
    app.router.routes.append(Route("/health", health, methods=["GET"]))
    app.add_middleware(PrivateResponses)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname], www_redirect=False)
    return app
