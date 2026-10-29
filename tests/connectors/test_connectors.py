"""Real MCP stdio -> HTTP -> native compiler and native Arrow UI interoperability."""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import copy
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from services.story_service.client import Client, load_bundle
from services.story_service.model import ServiceError

ROOT = Path(__file__).resolve().parents[2]


class ConnectorTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="arrow-connectors-")
        cls.work = Path(os.environ.get("ARROW_CONNECTOR_ARTIFACTS", cls.temporary.name))
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.token = secrets.token_urlsafe(32)
        cls.godot = shutil.which(os.environ.get("ARROW_STORY_GODOT", "godot"))
        if not cls.godot:
            raise RuntimeError("Godot is required")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}"
        cls.client = Client(cls.url, cls.token)
        cls.env = os.environ.copy()
        cls.env.update(ARROW_STORY_URL=cls.url, ARROW_STORY_API_TOKEN=cls.token,
                       ARROW_STORY_DB=str(cls.work / "stories.sqlite3"), ARROW_STORY_GODOT=cls.godot)
        cls.log = (cls.work / "service.log").open("w")
        cls.process = subprocess.Popen([sys.executable, "-m", "services.story_service", "serve", "--port", str(port)],
                                       cwd=ROOT, env=cls.env, stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(150):
            if cls.process.poll() is not None:
                raise RuntimeError("Service startup failed")
            try:
                if cls.client.request("GET", "/health")["status"] == "ok":
                    break
            except ServiceError:
                time.sleep(0.1)
        else:
            cls.process.terminate()
            raise RuntimeError("Service startup timed out")
        cls.fixture = load_bundle(ROOT / "examples/dialogic_bridge/story/guild_story.json")
        cls.arrow = cls.work / "arrow"
        shutil.copytree(ROOT, cls.arrow, ignore=shutil.ignore_patterns(".git", ".godot", "__pycache__", ".arrow.config", "*.log"))
        env = cls.env.copy()
        for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            env[key] = str(cls.work / key)
        result = subprocess.run([cls.godot, "--headless", "--path", str(cls.arrow), "--editor", "--import"],
                                env=env, capture_output=True, text=True, timeout=120)
        (cls.work / "editor-import.log").write_text(result.stdout + result.stderr)
        if result.returncode or re.search(r"(?m)^(SCRIPT ERROR:|ERROR:)", result.stdout + result.stderr):
            raise RuntimeError("Arrow import failed: " + (result.stdout + result.stderr)[-4000:])

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait()
        cls.log.close()
        cls.temporary.cleanup()

    async def fresh(self):
        bundle = copy.deepcopy(self.fixture)
        bundle["package"]["id"] = "test_" + uuid.uuid4().hex[:12]
        await asyncio.to_thread(self.client.request, "POST", "/v1/stories", {"bundle": bundle, "message": "Import non-canon fixture"})
        return bundle["package"]["id"]

    @asynccontextmanager
    async def session(self, *, apply=False, token=None):
        env = self.env.copy()
        if token is not None:
            env["ARROW_STORY_API_TOKEN"] = token
        parameters = StdioServerParameters(command=sys.executable,
            args=["-m", "services.story_mcp", "--url", self.url] + (["--allow-apply"] if apply else []),
            cwd=str(ROOT), env=env)
        with (self.work / ("mcp-" + uuid.uuid4().hex + ".log")).open("w") as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    async def call(self, session, name, args=None):
        result = await session.call_tool(name, args or {})
        self.assertFalse(result.isError, str(result.content))
        return result.structuredContent or json.loads(result.content[0].text)

    async def propose(self, session, story, content="A request revised through MCP.", revision=1):
        return await self.call(session, "propose_story_edits", {"story_id": story, "base_revision": revision,
            "operations": [{"op": "update_node", "document": "briefing", "node": "2", "data": {"content": content}}],
            "message": "Review MCP edit"})

    async def test_stdio_discovery_schema_resource_and_authentication(self):
        async with self.session() as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            self.assertEqual(set(tools), {"list_stories", "read_story", "list_story_revisions", "list_story_proposals", "read_story_proposal",
                                          "propose_story_edits", "story_head", "story_changes"})
            self.assertTrue(tools["read_story"].annotations.readOnlyHint)
            self.assertFalse(tools["propose_story_edits"].annotations.readOnlyHint)
            self.assertIn("oneOf", json.dumps(tools["propose_story_edits"].inputSchema))
            resources = await session.list_resources()
            self.assertIn("arrow://story-contract", [str(item.uri) for item in resources.resources])
            contract = await session.read_resource("arrow://story-contract")
            self.assertIn("restore_revision", contract.contents[0].text)
            await self.call(session, "list_stories")
        async with self.session(token="x" * 40) as session:
            result = await session.call_tool("list_stories", {})
            self.assertTrue(result.isError)
            self.assertIn("unauthorized", result.content[0].text)
            self.assertNotIn(self.token, result.model_dump_json())

    async def test_proposal_review_explicit_apply_restore_and_history(self):
        story = await self.fresh()
        async with self.session(apply=True) as session:
            before = await self.call(session, "read_story", {"story_id": story})
            proposal = await self.propose(session, story)
            self.assertEqual(proposal["status"], "ready")
            self.assertNotIn("candidate", proposal)
            self.assertEqual((await self.call(session, "read_story", {"story_id": story}))["revision"], 1)
            reviewed = await self.call(session, "read_story_proposal", {"story_id": story, "proposal_id": proposal["id"]})
            self.assertTrue(reviewed["validation"]["ok"])
            self.assertEqual(reviewed["diff"][0]["before"], before["bundle"]["documents"]["briefing"]["resources"]["nodes"]["2"]["data"]["content"])
            args = {"story_id": story, "proposal_id": proposal["id"]}
            applied = await self.call(session, "apply_story_proposal", args)
            self.assertEqual(applied["revision"], 2)
            self.assertNotIn("bundle", applied)
            self.assertEqual(await self.call(session, "apply_story_proposal", args), applied)
            restored = await self.call(session, "propose_story_edits", {"story_id": story, "base_revision": 2,
                "operations": [{"op": "restore_revision", "revision": 1}], "message": "Restore initial source"})
            await self.call(session, "apply_story_proposal", {"story_id": story, "proposal_id": restored["id"]})
            self.assertEqual((await self.call(session, "read_story", {"story_id": story}))["bundle"], before["bundle"])
            history = await self.call(session, "list_story_revisions", {"story_id": story, "after": 1, "limit": 1})
            self.assertEqual([item["revision"] for item in history["revisions"]], [2])
            queue = await self.call(session, "list_story_proposals", {"story_id": story, "limit": 1})
            self.assertEqual(queue["proposals"][0]["status"], "applied")
            full = await self.call(session, "read_story_proposal", {**args, "include_candidate": True})
            self.assertIn("candidate", full)

    async def test_live_commits_create_resources_and_follow_other_editors(self):
        story = await self.fresh()
        async with self.session(apply=True) as session:
            live = await self.call(session, "commit_story_edits", {"story_id": story, "base_revision": 1, "message": "Add a beat live",
                "operations": [{"op": "add_node", "document": "briefing", "type": "content", "ref": "beat", "data": {"content": "A live beat."}},
                               {"op": "set_connection", "document": "briefing", "node": "9", "slot": 0, "target": "@beat"}]})
            self.assertEqual((live["revision"], live["changed"]), (2, True))
            self.assertNotIn("bundle", live)
            self.assertIn("/documents/briefing/resources/nodes/" + str(1 << 37), [change["path"] for change in live["diff"]])
            # Someone editing in Arrow commits next; the agent sees what changed.
            Client(self.url, self.token).request("POST", f"/v1/stories/{story}/commits", {"base_revision": 2, "message": "Edited in Arrow",
                "operations": [{"op": "update_node", "document": "briefing", "node": "2", "data": {"content": "Typed in Arrow."}}]})
            head = await self.call(session, "story_head", {"story_id": story, "after_revision": 2, "wait_seconds": 5})
            self.assertEqual(head["revision"], 3)
            seen = await self.call(session, "story_changes", {"story_id": story, "from_revision": 2})
            self.assertEqual([change["after"] for change in seen["diff"]], ["Typed in Arrow."])
            clash = await session.call_tool("commit_story_edits", {"story_id": story, "base_revision": 2, "message": "Stale edit",
                "operations": [{"op": "update_node", "document": "briefing", "node": "2", "data": {"content": "Agent text."}}]})
            self.assertTrue(clash.isError)
            self.assertIn("edit_conflict", clash.content[0].text)

    async def test_invalid_and_stale_edits_cannot_commit(self):
        story = await self.fresh()
        async with self.session(apply=True) as session:
            invalid = await self.propose(session, story, "[signal=oops]")
            self.assertEqual(invalid["status"], "invalid")
            error = await session.call_tool("apply_story_proposal", {"story_id": story, "proposal_id": invalid["id"]})
            self.assertTrue(error.isError)
            self.assertIn("validation_failed", error.content[0].text)
            first = await self.propose(session, story, "First writer.")
            stale = await self.propose(session, story, "Second writer.")
            await self.call(session, "apply_story_proposal", {"story_id": story, "proposal_id": first["id"]})
            error = await session.call_tool("apply_story_proposal", {"story_id": story, "proposal_id": stale["id"]})
            self.assertTrue(error.isError)
            self.assertIn("revision_conflict", error.content[0].text)
            self.assertIn("current_revision", error.content[0].text)
            old = await self.call(session, "read_story", {"story_id": story, "revision": 1})
            self.assertEqual(old["revision"], 1)
            for operations in ([{"op": "shell", "command": "oops"}], [{"op": "update_node", "document": "briefing", "node": "2", "unknown": True}]):
                error = await session.call_tool("propose_story_edits", {"story_id": story, "base_revision": 2,
                    "operations": operations, "message": "Rejected malformed edit"})
                self.assertTrue(error.isError)
            self.assertEqual((await self.call(session, "read_story", {"story_id": story}))["revision"], 2)

    async def test_shared_registry_schema_keeps_boolean_type(self):
        story = await self.fresh()
        async with self.session() as session:
            proposal = await self.call(session, "propose_story_edits", {"story_id": story, "base_revision": 1,
                "operations": [{"op": "set_variable_default", "key": "request_accepted", "value": True}], "message": "Change authored default"})
            self.assertEqual(proposal["status"], "ready")
            self.assertEqual(len(proposal["diff"]), 3)
            self.assertTrue(all(item["after"] is True for item in proposal["diff"]))

    def run_ui(self, story, proposal, mode="edit", extra=None, script="test_story_ui.gd"):
        env = self.env.copy()
        env.update(ARROW_TEST_STORY=story, ARROW_TEST_PROPOSAL=proposal, ARROW_TEST_MODE=mode,
                   ARROW_TEST_OUTPUT=str(self.work / "ui-result.json"))
        env.update(extra or {})
        for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            env[key] = str(self.work / ("ui-" + story) / key)
        visual = bool(os.environ.get("ARROW_CONNECTOR_VISUAL"))
        command = [self.godot, *(["--rendering-method", "gl_compatibility", "--audio-driver", "Dummy"] if visual else ["--headless"]),
            "--path", str(self.arrow), "--script", "res://tests/connectors/" + script, "--config-dir", "user://"]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
        (self.work / ("ui-" + mode + ".log")).write_text(output)
        self.assertEqual(result.returncode, 0, output[-9000:])
        self.assertNotRegex(output, r"(?m)^(SCRIPT ERROR:|ERROR:)")
        self.assertIn("Arrow service UI: PASS", output)
        self.assertNotIn(self.token, output)
        # Token must not leak into persisted local source, configuration, or binding metadata.
        for path in (self.work / ("ui-" + story)).rglob("*"):
            if path.is_file():
                self.assertNotIn(self.token.encode(), path.read_bytes(), str(path))
        return json.loads((self.work / "ui-result.json").read_text())

    async def test_mcp_proposal_arrow_review_edit_restart_and_mcp_apply(self):
        story = await self.fresh()
        async with self.session(apply=True) as session:
            proposal = await self.propose(session, story)
            # Native UI opens revision 1, reviews and applies MCP proposal -> revision 2,
            # rejects its own stale edit, opens fresh r2 and proposes graph edit -> no silent commit.
            result = await asyncio.to_thread(self.run_ui, story, proposal["id"])
            self.assertEqual(self.client.request("GET", f"/v1/stories/{story}")["revision"], 2)
            ui_proposal = await self.call(session, "read_story_proposal", {"story_id": story, "proposal_id": result["proposal"]})
            self.assertEqual(ui_proposal["status"], "ready")
            self.assertTrue(any(item["after"] == "Changed inside Arrow." for item in ui_proposal["diff"]))
            # Restart editor and verify binding revision remains 2 and can propose another local edit.
            restarted = await asyncio.to_thread(self.run_ui, story, result["proposal"], "restart")
            self.assertEqual(restarted["base_revision"], 2)
            committed = await self.call(session, "apply_story_proposal", {"story_id": story, "proposal_id": result["proposal"]})
            self.assertEqual(committed["revision"], 3)
            head = await self.call(session, "read_story", {"story_id": story})
            self.assertEqual(head["bundle"]["documents"]["briefing"]["resources"]["nodes"]["2"]["data"]["content"], "Changed inside Arrow.")

    async def test_native_ui_imports_manifest_and_proposes_without_cli(self):
        story = "import_" + uuid.uuid4().hex[:12]
        bundle = copy.deepcopy(self.fixture)
        bundle["package"]["id"] = story
        source = self.work / story
        source.mkdir()
        (source / bundle["manifest_path"]).write_text(json.dumps(bundle["package"]))
        for chapter, spec in bundle["package"]["documents"].items():
            (source / spec["path"]).write_text(json.dumps(bundle["documents"][chapter]))
        result = await asyncio.to_thread(self.run_ui, story, "", "import",
            {"ARROW_TEST_MANIFEST": str(source / bundle["manifest_path"])})
        self.assertEqual(result["base_revision"], 1)
        self.assertEqual(self.client.request("GET", f"/v1/stories/{story}")["bundle"], bundle)

    async def test_native_transport_auth_redirect_limit_and_source_paths(self):
        seen = []
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/redirected")
                self.end_headers()
            def log_message(self, *args):
                pass
        redirect = HTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=redirect.serve_forever, daemon=True)
        thread.start()
        try:
            result = await asyncio.to_thread(self.run_ui, "transport", "", "transport",
                {"ARROW_TEST_REDIRECT_URL": f"http://127.0.0.1:{redirect.server_port}"}, "test_story_transport.gd")
            self.assertTrue(result["passed"])
            self.assertEqual(seen, ["/v1/stories"])
        finally:
            await asyncio.to_thread(redirect.shutdown)
            redirect.server_close()
            thread.join()

    async def test_visual_settings_shared_definitions_links_and_conflicts(self):
        story = "settings_" + uuid.uuid4().hex[:12]
        bundle = copy.deepcopy(self.fixture)
        bundle["package"]["id"] = story
        for key, kind, initial in (("counter", "num", 7), ("token_name", "str", "jade token")):
            bundle["package"]["registry"]["variables"][key] = {"name": key, "type": kind, "init": initial}
            for chapter, spec in bundle["package"]["documents"].items():
                uid = str((300 if key == "counter" else 301) + (1000 if chapter == "followup" else 0))
                spec["variables"][uid] = key
                bundle["documents"][chapter]["resources"]["variables"][uid] = {"name": key, "type": kind, "init": initial}
        await asyncio.to_thread(self.client.request, "POST", "/v1/stories", {"bundle": bundle, "message": "Non-canon typed settings fixture"})
        result = await asyncio.to_thread(self.run_ui, story, "", "settings", None, "test_story_settings.gd")
        original = self.client.request("GET", f"/v1/stories/{story}/revisions/1")
        self.assertEqual(original["bundle"], bundle)
        shared = self.client.request("GET", f"/v1/stories/{story}/revisions/2")["bundle"]
        self.assertEqual(shared["package"]["registry"]["characters"]["rend"]["name"], "Rend of the Lantern Guild")
        for chapter, spec in shared["package"]["documents"].items():
            for uid in spec["characters"]:
                self.assertEqual(shared["documents"][chapter]["resources"]["characters"][uid]["name"], "Rend of the Lantern Guild")
            for uid, key in spec["variables"].items():
                actual = shared["documents"][chapter]["resources"]["variables"][uid]["init"]
                expected = {"request_accepted": True, "counter": -12, "token_name": "copper token"}[key]
                self.assertIs(type(actual), type(expected))
                self.assertEqual(actual, expected)
        local = self.client.request("GET", f"/v1/stories/{story}")
        self.assertEqual(local["revision"], 3)
        self.assertEqual(local["bundle"]["package"]["links"], [])
        self.assertEqual(local["bundle"]["documents"]["briefing"]["resources"]["nodes"]["10"]["data"]["target"], 2)
        async with self.session(apply=True) as session:
            proposal = await self.call(session, "read_story_proposal", {"story_id": story, "proposal_id": result["proposal"]})
            self.assertEqual(proposal["status"], "ready")
            await self.call(session, "apply_story_proposal", {"story_id": story, "proposal_id": result["proposal"]})
        head = self.client.request("GET", f"/v1/stories/{story}")
        self.assertEqual(head["revision"], 4)
        self.assertEqual(head["bundle"]["documents"]["briefing"]["resources"]["nodes"]["10"]["data"]["target"], -1)
        self.assertEqual(head["bundle"]["package"]["links"][0]["to"], {"document": "followup", "node": "1"})

    async def test_visual_settings_empty_registries_and_no_jumps(self):
        story = "empty_" + uuid.uuid4().hex[:12]
        bundle = copy.deepcopy(self.fixture)
        bundle["package"]["id"] = story
        bundle["package"]["registry"] = {}
        bundle["package"]["links"] = []
        bundle["package"]["documents"] = {"briefing": {"path": "briefing.arrow"}}
        document = bundle["documents"]["briefing"]
        document["resources"]["characters"] = {}
        document["resources"]["variables"] = {}
        document["resources"]["nodes"] = {key: value for key, value in document["resources"]["nodes"].items() if key in ("1", "2")}
        scene = document["resources"]["scenes"]["0"]
        scene["map"] = {"1": {"offset": [0, 0], "io": [[1, 0, 2, 0]]}, "2": {"offset": [300, 0]}}
        bundle["documents"] = {"briefing": document}
        await asyncio.to_thread(self.client.request, "POST", "/v1/stories", {"bundle": bundle, "message": "Non-canon minimal settings fixture"})
        result = await asyncio.to_thread(self.run_ui, story, "", "empty-settings", None, "test_story_settings.gd")
        self.assertTrue(result["empty"])
        self.assertEqual(self.client.request("GET", f"/v1/stories/{story}")["revision"], 1)


if __name__ == "__main__":
    unittest.main()
