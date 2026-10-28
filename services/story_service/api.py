"""Authenticated HTTP interface. Mutation always goes through reviewed proposals."""
from __future__ import annotations
import os
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import urlsplit

from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse

from .model import ServiceError, parse_json
from .store import Store
from .validator import GodotValidator


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        count = 0
        started = False

        async def limited_receive():
            nonlocal count
            message = await receive()
            if message["type"] == "http.request":
                count += len(message.get("body", b""))
                if count > self.limit:
                    raise BodyTooLarge()
            return message

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except BodyTooLarge:
            if started:
                raise
            response = JSONResponse({"error": {"code": "body_too_large", "message": "Request exceeds the source upload limit"}}, status_code=413)
            await response(scope, receive, send)


class NoApiCache:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def no_cache(message):
            if message["type"] == "http.response.start" and scope.get("path", "").startswith("/v1/"):
                message["headers"] = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"cache-control"] + [(b"cache-control", b"no-store")]
            await send(message)
        return await self.app(scope, receive, no_cache)


def browser_origins() -> list[str]:
    values = parse_json(os.environ.get("ARROW_STORY_ALLOWED_ORIGINS", "[]"))
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise RuntimeError("ARROW_STORY_ALLOWED_ORIGINS must be a JSON array of exact HTTP(S) origins")
    for value in values:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or "*" in value
                or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
            raise RuntimeError("Browser origins must be exact HTTPS origins (HTTP is allowed on loopback)")
    return values


def create_app(*, store: Store | None = None, token: str | None = None, max_body: int = 8 * 1024 * 1024) -> FastAPI:
    token = token if token is not None else os.environ.get("ARROW_STORY_API_TOKEN", "")
    if not isinstance(token, str) or len(token) < 32 or any(not 33 <= ord(char) <= 126 for char in token):
        raise RuntimeError("Set ARROW_STORY_API_TOKEN to an ASCII bearer secret of at least 32 characters")
    if store is None:
        validator = GodotValidator(os.environ.get("ARROW_STORY_GODOT", "godot"))
        store = Store(Path(os.environ.get("ARROW_STORY_DB", ".arrow-service/stories.sqlite3")), validator)
    app = FastAPI(title="Arrow Story Service", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.add_middleware(BodyLimit, limit=max_body)
    app.add_middleware(NoApiCache)
    app.add_middleware(CORSMiddleware, allow_origins=browser_origins(), allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type"], allow_credentials=False)

    async def authorize(authorization: str | None = Header(default=None)):
        if authorization is None or not authorization.startswith("Bearer ") or not secrets.compare_digest(authorization[7:].encode(), token.encode()):
            raise ServiceError(401, "unauthorized", "A valid bearer token is required")

    async def body(request: Request, fields: set) -> dict:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ServiceError(415, "json_required", "Use application/json")
        value = parse_json(await request.body())
        if not isinstance(value, dict) or set(value) != fields:
            raise ServiceError(422, "invalid_request", "Request requires exactly: " + ", ".join(sorted(fields)))
        return value

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse({"error": {"code": error.code, "message": error.message, **error.details}}, status_code=error.status,
                            headers={"WWW-Authenticate": "Bearer"} if error.status == 401 else None)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request, error):
        return JSONResponse({"error": {"code": "database_unavailable", "message": "Database operation failed; retry or inspect server storage"}}, status_code=503)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/stories", dependencies=[Depends(authorize)])
    def stories():
        return {"stories": store.list_stories()}

    @app.post("/v1/stories", dependencies=[Depends(authorize)], status_code=201)
    async def create(request: Request):
        value = await body(request, {"bundle", "message"})
        # Native validation is blocking; run it outside the ASGI event loop.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(store.create, value["bundle"], value["message"])

    @app.get("/v1/stories/{story_id}", dependencies=[Depends(authorize)])
    def read(story_id: str):
        return store.read(story_id)

    @app.get("/v1/stories/{story_id}/revisions", dependencies=[Depends(authorize)])
    def history(story_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        return {"revisions": store.history(story_id, after, limit)}

    @app.get("/v1/stories/{story_id}/revisions/{revision}", dependencies=[Depends(authorize)])
    def revision(story_id: str, revision: int):
        return store.read(story_id, revision)

    @app.post("/v1/stories/{story_id}/proposals", dependencies=[Depends(authorize)], status_code=201)
    async def propose(story_id: str, request: Request):
        value = await body(request, {"base_revision", "operations", "message"})
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(store.propose, story_id, value["base_revision"], value["operations"], value["message"])

    @app.get("/v1/stories/{story_id}/proposals", dependencies=[Depends(authorize)])
    def proposals(story_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        return {"proposals": store.proposals(story_id, after, limit)}

    @app.get("/v1/stories/{story_id}/proposals/{proposal_id}", dependencies=[Depends(authorize)])
    def proposal(story_id: str, proposal_id: str):
        return store.proposal(story_id, proposal_id)

    @app.post("/v1/stories/{story_id}/proposals/{proposal_id}/apply", dependencies=[Depends(authorize)])
    async def apply(story_id: str, proposal_id: str, request: Request):
        await body(request, set())
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(store.apply, story_id, proposal_id)

    web_root = os.environ.get("ARROW_STORY_WEB_ROOT")
    if web_root:
        path = Path(web_root).resolve()
        if not (path / "index.html").is_file():
            raise RuntimeError("ARROW_STORY_WEB_ROOT must contain an exported PWA index.html")
        app.mount("/app", StaticFiles(directory=path, html=True), name="arrow-pwa")
    return app
