"""HTTP client and safe Arrow source checkout; bearer tokens never enter argv."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from .model import ServiceError, bundle_shape, canonical, parse_json, source_text


class Client:
    def __init__(self, url: str, token: str | None = None):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Supply an HTTP(S) service URL without credentials, query or fragment")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Remote service connections require HTTPS")
        self.url = url.rstrip("/")
        self.token = token if token is not None else os.environ.get("ARROW_STORY_API_TOKEN", "")
        if not self.token or not self.token.isascii():
            raise ValueError("Set ARROW_STORY_API_TOKEN in the client environment")
        # A Cloudflare Access service token lets non-browser clients through Access in front of the service.
        access_id, access_secret = os.environ.get("ARROW_STORY_ACCESS_CLIENT_ID", ""), os.environ.get("ARROW_STORY_ACCESS_CLIENT_SECRET", "")
        if (access_id or access_secret) and not (access_id and access_secret and access_id.isascii() and access_secret.isascii()):
            raise ValueError("Set both ARROW_STORY_ACCESS_CLIENT_ID and ARROW_STORY_ACCESS_CLIENT_SECRET, or neither")
        self.access_headers = {"CF-Access-Client-Id": access_id, "CF-Access-Client-Secret": access_secret} if access_id else {}
        # Do not forward the bearer token to redirects, including a different host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method: str, path: str, value=None):
        request = urllib.request.Request(self.url + path, method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json", **self.access_headers},
            data=None if value is None else canonical(value).encode())
        try:
            with self.opener.open(request, timeout=90) as response:
                return parse_json(response.read())
        except urllib.error.HTTPError as error:
            try:
                details = parse_json(error.read())["error"]
            except (ServiceError, KeyError, TypeError):
                raise ServiceError(error.code, "http_error", f"Service returned HTTP {error.code}") from error
            finally:
                error.close()
            raise ServiceError(error.code, details.pop("code"), details.pop("message"), **details) from error
        except urllib.error.URLError as error:
            raise ServiceError(503, "connection_failed", "Could not connect to the story service") from error


def load_bundle(manifest: str | Path) -> dict:
    manifest = Path(manifest)
    if manifest.is_symlink():
        raise ValueError("Manifest symlinks are not supported")
    package = parse_json(manifest.read_text(encoding="utf-8"))
    # Check every untrusted path before reading dependency contents.
    if not isinstance(package, dict) or not isinstance(package.get("documents"), dict):
        raise ValueError("Manifest needs a documents object")
    bundle = {"manifest_path": manifest.name, "package": package,
              "documents": {chapter: {"resources": {key: {} for key in ("nodes", "scenes", "characters", "variables")}} for chapter in package["documents"]}}
    bundle_shape(bundle)
    for chapter, spec in package["documents"].items():
        target = manifest.parent
        for part in spec["path"].split("/"):
            target = target / part
            if target.is_symlink():
                raise ValueError("Chapter symlinks are not supported")
        bundle["documents"][chapter] = parse_json(target.read_text(encoding="utf-8"))
    bundle_shape(bundle)
    return bundle


def checkout(client: Client, story_id: str, target: str | Path, revision: int | None = None) -> dict:
    route = "/v1/stories/" + urllib.parse.quote(story_id, safe="")
    if revision is not None:
        route += "/revisions/" + str(revision)
    result = client.request("GET", route)
    bundle = result["bundle"]
    bundle_shape(bundle)
    target = Path(target).absolute()
    if target.exists() or target.is_symlink():
        raise ValueError("Checkout requires a new destination; existing files are never replaced")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".arrow-checkout-", dir=target.parent))
    try:
        (temporary / bundle["manifest_path"]).write_text(source_text(bundle["package"]), encoding="utf-8")
        for chapter, spec in bundle["package"]["documents"].items():
            path = temporary / spec["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source_text(bundle["documents"][chapter]), encoding="utf-8")
        (temporary / ".arrow-story-checkout.json").write_text(source_text({"service": client.url, "story_id": story_id,
            "revision": result["revision"], "sha256": result["sha256"], "manifest_path": bundle["manifest_path"]}), encoding="utf-8")
        # Avoid replacing even an empty directory created by another process.
        target.mkdir()
        for child in temporary.iterdir():
            shutil.move(str(child), target / child.name)
        temporary.rmdir()
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"directory": str(target), "story_id": story_id, "revision": result["revision"]}
