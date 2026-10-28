"""Run with python -m services.story_service; see docs/story-service.md."""
import argparse
import os
from pathlib import Path
import sys
import urllib.parse

from .client import Client, checkout, load_bundle
from .model import ServiceError, parse_json, source_text, source_path


def main():
    parser = argparse.ArgumentParser(description="Arrow story service and source client")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--workers", type=int, default=1)
    serve.add_argument("--web-root", type=Path, help="Serve an exported Arrow PWA at /app/")
    backup = sub.add_parser("backup")
    backup.add_argument("database", type=Path)
    backup.add_argument("destination", type=Path)
    create = sub.add_parser("import")
    create.add_argument("manifest", type=Path)
    create.add_argument("--message", required=True)
    fetch = sub.add_parser("checkout")
    fetch.add_argument("story")
    fetch.add_argument("directory", type=Path)
    fetch.add_argument("--revision", type=int)
    proposal = sub.add_parser("propose")
    proposal.add_argument("story")
    proposal.add_argument("--base", type=int, required=True)
    proposal.add_argument("--operations", type=Path, required=True)
    proposal.add_argument("--message", required=True)
    push = sub.add_parser("push")
    push.add_argument("directory", type=Path)
    push.add_argument("--message", required=True)
    read = sub.add_parser("read")
    read.add_argument("story")
    queue = sub.add_parser("proposals")
    queue.add_argument("story")
    review = sub.add_parser("review")
    review.add_argument("story")
    review.add_argument("proposal")
    apply = sub.add_parser("apply")
    apply.add_argument("story")
    apply.add_argument("proposal")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            if args.web_root:
                os.environ["ARROW_STORY_WEB_ROOT"] = str(args.web_root.resolve())
            import uvicorn
            uvicorn.run("services.story_service.api:create_app", factory=True, host=args.host, port=args.port,
                        workers=args.workers, access_log=False)
            return
        if args.command == "backup":
            from .store import Store
            if not args.database.is_file():
                raise ValueError("Existing service database is required")
            Store(args.database, None).backup(args.destination)
            print(source_text({"backup": str(args.destination)}), end="")
            return
        client = Client(args.url)
        if args.command == "import":
            result = client.request("POST", "/v1/stories", {"bundle": load_bundle(args.manifest), "message": args.message})
        elif args.command == "checkout":
            result = checkout(client, args.story, args.directory, args.revision)
        elif args.command == "push":
            metadata = parse_json((args.directory / ".arrow-story-checkout.json").read_text())
            if metadata["service"] != client.url:
                raise ValueError("Checkout belongs to another service URL")
            manifest_name = source_path(metadata["manifest_path"])
            if "/" in manifest_name:
                raise ValueError("Checkout manifest must remain inside the checkout directory")
            result = client.request("POST", "/v1/stories/" + urllib.parse.quote(metadata["story_id"], safe="") + "/proposals",
                {"base_revision": metadata["revision"], "message": args.message, "operations": [
                    {"op": "replace_bundle", "bundle": load_bundle(args.directory / manifest_name)}]})
        else:
            path = "/v1/stories/" + urllib.parse.quote(args.story, safe="")
            if args.command == "read":
                result = client.request("GET", path)
            elif args.command == "proposals":
                result = client.request("GET", path + "/proposals")
            elif args.command == "review":
                result = client.request("GET", path + "/proposals/" + urllib.parse.quote(args.proposal, safe=""))
            elif args.command == "propose":
                result = client.request("POST", path + "/proposals", {"base_revision": args.base,
                    "operations": parse_json(args.operations.read_text()), "message": args.message})
            else:
                result = client.request("POST", path + "/proposals/" + urllib.parse.quote(args.proposal, safe="") + "/apply", {})
        print(source_text(result), end="")
    except ServiceError as error:
        print(source_text({"error": {"status": error.status, "code": error.code, "message": error.message, **error.details}}), end="", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError) as error:
        print(source_text({"error": {"message": str(error)}}), end="", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
