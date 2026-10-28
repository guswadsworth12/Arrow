"""Optional Cloudflare Access identity: verify the signed assertion Access adds to proxied requests."""
from __future__ import annotations
import os
from urllib.parse import urlsplit

import jwt

from .model import ServiceError

BEARER_ACTOR = "bearer-token"


class AccessVerifier:
    """Checks `Cf-Access-Jwt-Assertion` against the team's published signing keys.

    `keys` replaces the JWKS client in tests; it needs `get_signing_key_from_jwt(token).key`.
    """

    def __init__(self, team_domain: str, audience: str, keys=None):
        parsed = urlsplit(team_domain.rstrip("/"))
        if (parsed.scheme != "https" or not parsed.hostname or parsed.port or parsed.path or parsed.query
                or parsed.fragment or parsed.username or parsed.password):
            raise RuntimeError("ARROW_STORY_ACCESS_TEAM_DOMAIN must be an https:// team domain, such as https://TEAM.cloudflareaccess.com")
        if not audience.isascii() or not audience.isalnum() or len(audience) > 128:
            raise RuntimeError("ARROW_STORY_ACCESS_AUD must be the Access application's Audience (AUD) tag")
        self.issuer = "https://" + parsed.hostname
        self.audience = audience
        self.keys = keys or jwt.PyJWKClient(self.issuer + "/cdn-cgi/access/certs", lifespan=3600, timeout=10)

    def identity(self, assertion: str) -> str:
        """Return the signed-in email, or `service-token:<client id>` for Access service tokens."""
        try:
            key = self.keys.get_signing_key_from_jwt(assertion).key
            claims = jwt.decode(assertion, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                                options={"require": ["exp", "iat", "aud", "iss"]}, leeway=30)
        except jwt.PyJWKClientConnectionError as error:
            raise ServiceError(503, "access_keys_unavailable", "Could not fetch Cloudflare Access signing keys") from error
        except jwt.PyJWTError as error:
            raise ServiceError(401, "unauthorized", "Cloudflare Access sign-in is invalid or expired") from error
        email, common_name = claims.get("email"), claims.get("common_name")
        if isinstance(email, str) and actor_text(email):
            return email
        if isinstance(common_name, str) and actor_text(common_name):
            return "service-token:" + common_name
        raise ServiceError(401, "unauthorized", "Cloudflare Access sign-in has no user or service identity")


def actor_text(value: str) -> bool:
    return 0 < len(value) <= 254 and all(33 <= ord(character) <= 126 for character in value)


def from_environment() -> AccessVerifier | None:
    team, audience = os.environ.get("ARROW_STORY_ACCESS_TEAM_DOMAIN", ""), os.environ.get("ARROW_STORY_ACCESS_AUD", "")
    if not team and not audience:
        return None
    if not team or not audience:
        raise RuntimeError("Set both ARROW_STORY_ACCESS_TEAM_DOMAIN and ARROW_STORY_ACCESS_AUD, or neither")
    return AccessVerifier(team, audience)
