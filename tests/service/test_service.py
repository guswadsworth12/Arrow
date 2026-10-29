"""Real HTTP, SQLite and native Godot tests for the optional story service."""
from __future__ import annotations
import concurrent.futures
import copy
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import uvicorn

from services.story_service import access as cloudflare_access
from services.story_service.access import AccessVerifier
from services.story_service.api import create_app
from services.story_service.client import Client, checkout, load_bundle
from services.story_service.model import ServiceError, apply_operations, base36, bundle_shape, changes, content_hash, parse_json, source_text
from services.story_service.store import Store
from services.story_service.validator import GodotValidator

ROOT = Path(__file__).resolve().parents[2]


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="arrow-service-tests-")
        cls.work = Path(cls.temporary.name)
        cls.validator = GodotValidator(os.environ.get("ARROW_STORY_GODOT", "godot"))
        cls.fixture = load_bundle(ROOT / "examples/dialogic_bridge/story/guild_story.json")
        cls.token = secrets.token_urlsafe(32)
        cls.database = cls.work / "live.sqlite3"
        cls.start_server()

    @classmethod
    def start_server(cls):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.client = Client(cls.url, cls.token)
        cls.log = (cls.work / ("server-" + uuid.uuid4().hex + ".log")).open("w")
        environment = os.environ.copy()
        environment.update(ARROW_STORY_API_TOKEN=cls.token, ARROW_STORY_DB=str(cls.database), ARROW_STORY_GODOT=cls.validator.godot)
        cls.process = subprocess.Popen([sys.executable, "-m", "services.story_service", "serve", "--port", str(cls.port)],
                                       cwd=ROOT, env=environment, stdout=cls.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                cls.log.close()
                raise RuntimeError("Test service exited at startup")
            try:
                if cls.client.request("GET", "/health")["status"] == "ok":
                    return
            except ServiceError:
                time.sleep(0.05)
        cls.stop_server()
        raise RuntimeError("Test service did not become healthy")

    @classmethod
    def stop_server(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait()
        cls.log.close()

    @classmethod
    def tearDownClass(cls):
        cls.stop_server()
        cls.temporary.cleanup()

    def fresh(self):
        bundle = copy.deepcopy(self.fixture)
        bundle["package"]["id"] = "test_" + uuid.uuid4().hex[:12]
        result = self.client.request("POST", "/v1/stories", {"bundle": bundle, "message": "Import non-canon example"})
        return result["story_id"], bundle

    def propose(self, story, operations, revision=1):
        return self.client.request("POST", f"/v1/stories/{story}/proposals", {"base_revision": revision, "operations": operations, "message": "Review this edit"})

    def apply(self, story, proposal):
        return self.client.request("POST", f"/v1/stories/{story}/proposals/{proposal['id']}/apply", {})

    def edit(self, text="A changed guild request."):
        return {"op": "update_node", "document": "briefing", "node": "2", "data": {"content": text}}

    def error(self, status, callback):
        with self.assertRaises(ServiceError) as captured:
            callback()
        self.assertEqual(captured.exception.status, status)
        return captured.exception

    def test_authentication_all_story_routes(self):
        story, _ = self.fresh()
        unauthenticated = Client(self.url, "invalid")
        for method, path, body in [
            ("GET", "/v1/session", None),
            ("GET", "/v1/stories", None), ("GET", f"/v1/stories/{story}", None),
            ("POST", "/v1/stories", {}), ("GET", f"/v1/stories/{story}/revisions", None),
            ("GET", f"/v1/stories/{story}/revisions/1", None),
            ("POST", f"/v1/stories/{story}/proposals", {}), ("GET", f"/v1/stories/{story}/proposals", None),
            ("GET", f"/v1/stories/{story}/proposals/missing", None),
            ("POST", f"/v1/stories/{story}/proposals/missing/apply", {}),
        ]:
            with self.subTest(path=path):
                self.error(401, lambda: unauthenticated.request(method, path, body))
        self.assertEqual(unauthenticated.request("GET", "/health"), {"status": "ok"})

    def test_propose_review_apply_and_idempotent_retry(self):
        story, bundle = self.fresh()
        proposal = self.propose(story, [self.edit()])
        self.assertEqual(proposal["status"], "ready")
        self.assertEqual(proposal["diff"][0]["path"], "/documents/briefing/resources/nodes/2/data/content")
        self.assertEqual(self.client.request("GET", f"/v1/stories/{story}")["revision"], 1)
        self.assertEqual(proposal["validation"]["fingerprint"], self.validator.fingerprint)
        committed = self.apply(story, proposal)
        self.assertEqual(committed["revision"], 2)
        self.assertEqual(self.apply(story, proposal)["sha256"], committed["sha256"])
        self.assertEqual(self.client.request("GET", f"/v1/stories/{story}/revisions/1")["bundle"], bundle)
        self.assertEqual(len(self.client.request("GET", f"/v1/stories/{story}/revisions")["revisions"]), 2)
        reviewed = self.client.request("GET", f"/v1/stories/{story}/proposals/{proposal['id']}")
        self.assertEqual(reviewed["status"], "applied")
        self.assertEqual(reviewed["applied_revision"], 2)
        queue = self.client.request("GET", f"/v1/stories/{story}/proposals")["proposals"]
        self.assertEqual(queue[0]["status"], "applied")
        self.assertNotIn("candidate", queue[0])
        self.assertEqual(self.client.request("GET", f"/v1/stories/{story}/proposals?after={queue[0]['sequence']}")["proposals"], [])

    def test_two_writers_cannot_overwrite_each_other(self):
        story, _ = self.fresh()
        proposals = [self.propose(story, [self.edit(text)]) for text in ("First writer.", "Second writer.")]
        barrier = threading.Barrier(2)
        def commit(proposal):
            barrier.wait()
            try:
                return self.apply(story, proposal)["revision"]
            except ServiceError as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(commit, proposals))
        self.assertEqual(sorted(outcomes), [2, 409])
        head = self.client.request("GET", f"/v1/stories/{story}")
        self.assertIn(head["bundle"]["documents"]["briefing"]["resources"]["nodes"]["2"]["data"]["content"], ["First writer.", "Second writer."])
        self.assertEqual(len(self.client.request("GET", f"/v1/stories/{story}/revisions")["revisions"]), 2)
        conflict = self.error(409, lambda: self.propose(story, [self.edit()], 1))
        self.assertEqual(conflict.details["current_revision"], 2)

    def test_invalid_native_content_never_commits(self):
        story, bundle = self.fresh()
        proposal = self.propose(story, [self.edit("[unmapped markup]")])
        self.assertEqual(proposal["status"], "invalid")
        self.assertFalse(proposal["validation"]["ok"])
        self.error(422, lambda: self.apply(story, proposal))
        self.assertEqual(self.client.request("GET", f"/v1/stories/{story}")["bundle"], bundle)
        invalid = copy.deepcopy(bundle)
        invalid["package"]["id"] = "invalid_" + uuid.uuid4().hex[:8]
        invalid["documents"]["followup"]["resources"]["variables"]["901"]["init"] = True
        self.error(422, lambda: self.client.request("POST", "/v1/stories", {"bundle": invalid, "message": "Invalid import"}))
        self.error(404, lambda: self.client.request("GET", "/v1/stories/" + invalid["package"]["id"]))

    def test_shared_registry_operations_sync_local_resources(self):
        story, _ = self.fresh()
        proposal = self.propose(story, [{"op": "set_character_name", "key": "rend", "value": "Rend the Envoy"},
                                      {"op": "set_variable_default", "key": "request_accepted", "value": True}])
        self.assertEqual(proposal["status"], "ready")
        bundle = self.apply(story, proposal)["bundle"]
        self.assertTrue(bundle["documents"]["briefing"]["resources"]["variables"]["200"]["init"])
        self.assertTrue(bundle["documents"]["followup"]["resources"]["variables"]["901"]["init"])
        for chapter, uid in [("briefing", "100"), ("followup", "900")]:
            self.assertEqual(bundle["documents"][chapter]["resources"]["characters"][uid]["name"], "Rend the Envoy")
        self.assertGreaterEqual(len(proposal["diff"]), 6)

    def test_graph_connections_and_links(self):
        story, _ = self.fresh()
        proposal = self.propose(story, [{"op": "set_connection", "document": "briefing", "node": "3", "slot": 1, "target": "6"},
                                      {"op": "set_link", "document": "briefing", "node": "10", "target_document": "followup", "target_node": "2"}])
        self.assertEqual(proposal["status"], "ready")
        bundle = self.apply(story, proposal)["bundle"]
        self.assertEqual(bundle["package"]["links"][0]["to"]["node"], "2")
        self.assertIn([3, 1, 6, 0], bundle["documents"]["briefing"]["resources"]["scenes"]["0"]["map"]["3"]["io"])
        detached = self.propose(story, [{"op": "disconnect", "document": "briefing", "node": "3", "slot": 1}], 2)
        self.assertEqual(detached["status"], "ready")
        self.error(422, lambda: self.propose(story, [{"op": "set_connection", "document": "briefing", "node": "1", "slot": True, "target": "2"}], 2))

    def test_restore_creates_a_new_revision(self):
        story, original = self.fresh()
        self.apply(story, self.propose(story, [self.edit()]))
        restore = self.propose(story, [{"op": "restore_revision", "revision": 1}], 2)
        self.assertEqual(restore["status"], "ready")
        restored = self.apply(story, restore)
        self.assertEqual(restored["revision"], 3)
        self.assertEqual(restored["bundle"], original)
        self.assertNotEqual(self.client.request("GET", f"/v1/stories/{story}/revisions/2")["bundle"], original)
        page = self.client.request("GET", f"/v1/stories/{story}/revisions?after=1&limit=1")
        self.assertEqual([row["revision"] for row in page["revisions"]], [2])

    def test_checkout_round_trip_and_no_overwrite(self):
        story, original = self.fresh()
        directory = self.work / uuid.uuid4().hex
        result = checkout(self.client, story, directory)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(load_bundle(directory / original["manifest_path"]), original)
        with self.assertRaises(ValueError):
            checkout(self.client, story, directory)
        self.assertEqual(load_bundle(directory / original["manifest_path"]), original)
        environment = os.environ.copy()
        environment["ARROW_STORY_API_TOKEN"] = self.token
        chapter = directory / original["package"]["documents"]["briefing"]["path"]
        data = parse_json(chapter.read_text())
        data["resources"]["nodes"]["2"]["data"]["content"] = "Edited in the Arrow checkout."
        chapter.write_text(source_text(data))
        process = subprocess.run([sys.executable, "-m", "services.story_service", "--url", self.url,
                                  "push", str(directory), "--message", "Round trip source files"], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        proposal = parse_json(process.stdout)
        self.assertEqual(proposal["status"], "ready")
        self.assertEqual(self.client.request("GET", f"/v1/stories/{story}")["revision"], 1)
        self.apply(story, proposal)
        for arguments in [["review", story, proposal["id"]], ["proposals", story], ["apply", story, proposal["id"]], ["read", story]]:
            command = subprocess.run([sys.executable, "-m", "services.story_service", "--url", self.url, *arguments], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=15)
            self.assertEqual(command.returncode, 0, command.stderr)
            self.assertIsInstance(parse_json(command.stdout), dict)
        self.assertEqual(parse_json((directory / ".arrow-story-checkout.json").read_text())["revision"], 1)
        self.error(409, lambda: self.propose(story, [self.edit()], 1))

    def test_durable_restart_and_consistent_backup(self):
        story, _ = self.fresh()
        proposal = self.propose(story, [self.edit()])
        committed = self.apply(story, proposal)
        backup = self.work / (uuid.uuid4().hex + ".sqlite3")
        local = Store(self.database, self.validator)
        local.backup(backup)
        with self.assertRaises(FileExistsError):
            local.backup(backup)
        restored = Store(backup, self.validator)
        self.assertEqual(restored.read(story), committed)
        self.assertEqual(restored.proposal(story, proposal["id"])["status"], "applied")
        type(self).stop_server()
        type(self).start_server()
        self.assertEqual(self.client.request("GET", f"/v1/stories/{story}"), committed)
        self.assertEqual(self.apply(story, proposal)["revision"], 2)

    def test_json_validation_request_limits_and_no_blind_put(self):
        story, _ = self.fresh()
        self.error(405, lambda: self.client.request("PUT", f"/v1/stories/{story}", {}))
        self.error(422, lambda: self.propose(story, [self.edit()], True))
        self.error(422, lambda: self.propose(story, [{"op": "run_shell", "command": "ignored"}]))
        self.error(422, lambda: self.propose(story, [self.edit("A sealed request waits on the guild board.")]))
        for payload in [b'{"message":"one","message":"two"}', b'{"bundle":NaN,"message":"bad"}', json.dumps({"bundle": {}, "message": chr(0xD800)}).encode()]:
            request = urllib.request.Request(self.url + "/v1/stories", data=payload,
                headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as captured:
                urllib.request.urlopen(request)
            self.assertEqual(captured.exception.code, 422)
            captured.exception.close()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("POST", "/v1/stories", body=iter([b" " * 65536 for _ in range(130)]),
                           headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}, encode_chunked=True)
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        connection.close()
        self.assertEqual(self.client.request("GET", f"/v1/stories/{story}")["revision"], 1)

    def test_path_and_malformed_operation_guards(self):
        for path in (".arrow-story-checkout.json", "../outside.arrow", "/tmp/outside.arrow", "a/../x.arrow", "addons/evil.gd", "bad\x00.arrow", "a\\x.arrow"):
            bundle = copy.deepcopy(self.fixture)
            bundle["package"]["documents"]["briefing"]["path"] = path
            with self.subTest(path=repr(path)), self.assertRaises(ServiceError):
                bundle_shape(bundle)
        malformed = copy.deepcopy(self.fixture["documents"]["briefing"])
        malformed["resources"]["scenes"] = {"0": None}
        operations = [{"op": "replace_document", "document": "briefing", "value": malformed},
                      {"op": "disconnect", "document": "briefing", "node": "1", "slot": 0}]
        self.error(422, lambda: apply_operations(self.fixture, operations))
        self.assertTrue(changes(False, 0))
        self.assertNotEqual(content_hash(False), content_hash(0))
        for url in ("http://example.com", "https://user:password@example.com", "file:///tmp/test"):
            with self.assertRaises(ValueError):
                Client(url, self.token)

    def test_commit_revalidates_with_current_compiler(self):
        store = Store(self.work / (uuid.uuid4().hex + ".sqlite3"), self.validator)
        created = store.create(self.fixture, "Import example")
        proposal = store.propose(created["story_id"], 1, [self.edit()], "Originally valid")
        class ChangedCompiler:
            def validate(self, candidate):
                return {"ok": False, "errors": ["New compiler rejects this content"]}
        store.validator = ChangedCompiler()
        self.error(422, lambda: store.apply(created["story_id"], proposal["id"]))
        self.assertEqual(store.read(created["story_id"])["revision"], 1)
        store.validator = self.validator
        self.assertEqual(store.apply(created["story_id"], proposal["id"])["revision"], 2)

    def test_invalid_unicode_and_nested_input(self):
        bundle = copy.deepcopy(self.fixture)
        bundle["package"]["documents"]["briefing"]["path"] = "bad\ud800.arrow"
        self.error(422, lambda: bundle_shape(bundle))
        self.error(422, lambda: parse_json("[" * 1500 + "0" + "]" * 1500))
        changed = copy.deepcopy(self.fixture)
        changed["documents"]["briefing"]["resources"]["nodes"]["2"]["data"]["content"] = "x" * (8 * 1024 * 1024)
        self.error(422, lambda: bundle_shape(changed))

    def test_atomic_commit_rolls_back_after_head_update(self):
        store = Store(self.work / (uuid.uuid4().hex + ".sqlite3"), self.validator)
        created = store.create(self.fixture, "Import example")
        proposal = store.propose(created["story_id"], 1, [self.edit()], "Atomic publication")
        with store.connection(write=True) as connection:
            connection.execute("CREATE TRIGGER fail_apply BEFORE UPDATE ON proposals BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            store.apply(created["story_id"], proposal["id"])
        self.assertEqual(store.read(created["story_id"])["revision"], 1)
        self.assertEqual(len(store.history(created["story_id"])), 1)
        self.assertEqual(store.proposal(created["story_id"], proposal["id"])["status"], "ready")
        with store.connection(write=True) as connection:
            connection.execute("DROP TRIGGER fail_apply")
        self.assertEqual(store.apply(created["story_id"], proposal["id"])["revision"], 2)

    def test_created_resources_use_native_uids_and_batch_references(self):
        story, _ = self.fresh()
        proposal = self.propose(story, [
            {"op": "add_variable", "document": "followup", "name": "depot_searched", "type": "bool", "init": False, "ref": "searched"},
            {"op": "add_character", "document": "followup", "name": "Hesk", "color": "c8a050", "ref": "hesk"},
            {"op": "add_node", "document": "followup", "type": "monolog", "ref": "line",
             "data": {"character": "@hesk", "monolog": "The depot is this way."}},
            {"op": "add_node", "document": "followup", "type": "variable_update", "ref": "mark",
             "data": {"variable": "@searched", "operator": "set", "with": [0, True]}},
            {"op": "set_connection", "document": "followup", "node": "6", "slot": 0, "target": "@line"},
            {"op": "set_connection", "document": "followup", "node": "@line", "slot": 0, "target": "@mark"}])
        self.assertEqual(proposal["status"], "ready", proposal["validation"])
        bundle = self.apply(story, proposal)["bundle"]
        followup = bundle["documents"]["followup"]
        # Chapter 1, new author slot 1, seeds 0..3: Arrow's (chapter << 43) | (author << 37) | seed.
        base = (1 << 43) | (1 << 37)
        variable, character, line, mark = (str(base + seed) for seed in range(4))
        self.assertEqual(followup["meta"]["authors"]["1"], ["Arrow Story Service", 4])
        self.assertEqual(followup["resources"]["nodes"][line]["data"]["character"], int(character))
        self.assertEqual(followup["resources"]["nodes"][mark]["data"]["variable"], int(variable))
        self.assertEqual(followup["resources"]["nodes"][line]["name"], base36(int(line)))
        self.assertIn([int(line), 0, int(mark), 0], followup["resources"]["scenes"]["0"]["map"][line]["io"])
        self.assertEqual(bundle["package"]["documents"]["followup"]["variables"][variable], "depot_searched")
        self.assertEqual(bundle["package"]["registry"]["characters"]["Hesk"], {"name": "Hesk"})
        # The next batch continues the service author's seed; existing registry keys are reused.
        again = self.propose(story, [{"op": "add_node", "document": "briefing", "type": "content", "data": {"content": "Later."}},
                                    {"op": "add_character", "document": "briefing", "key": "Hesk"}], 2)
        self.assertEqual(again["status"], "ready", again["validation"])
        briefing = self.apply(story, again)["bundle"]["documents"]["briefing"]
        self.assertIn(str(1 << 37), briefing["resources"]["nodes"])
        self.assertEqual(briefing["resources"]["characters"][str((1 << 37) + 1)]["name"], "Hesk")
        self.error(422, lambda: self.propose(story, [{"op": "set_connection", "document": "briefing", "node": "2", "slot": 0, "target": "@missing"}], 3))
        self.error(422, lambda: self.propose(story, [{"op": "add_variable", "document": "briefing", "key": "request_accepted"}], 3))

    def test_remove_node_drops_wires_and_links(self):
        story, _ = self.fresh()
        proposal = self.propose(story, [{"op": "remove_node", "document": "briefing", "node": "9"},
                                      {"op": "set_connection", "document": "briefing", "node": "7", "slot": 0, "target": "8"}])
        self.assertEqual(proposal["status"], "ready", proposal["validation"])
        briefing = self.apply(story, proposal)["bundle"]["documents"]["briefing"]
        self.assertNotIn("9", briefing["resources"]["nodes"])
        self.assertNotIn("9", briefing["resources"]["scenes"]["0"]["map"])
        self.assertEqual(briefing["resources"]["scenes"]["0"]["map"]["7"]["io"], [[7, 1, 8, 0], [7, 0, 8, 0]])
        unlinked = self.propose(story, [{"op": "remove_node", "document": "briefing", "node": "10"}], 2)
        self.assertEqual(unlinked["candidate"]["package"]["links"], [])
        self.error(422, lambda: self.propose(story, [{"op": "remove_node", "document": "briefing", "node": "1"}], 2))

    def test_allocation_skips_used_uids_and_refuses_snowflake_chapters(self):
        bundle = copy.deepcopy(self.fixture)
        taken = str(1 << 37)
        bundle["documents"]["briefing"]["resources"]["nodes"][taken] = {"type": "content", "name": "taken", "data": {}}
        result = apply_operations(bundle, [{"op": "add_node", "document": "briefing", "type": "content"}])
        self.assertIn(str((1 << 37) + 1), result["documents"]["briefing"]["resources"]["nodes"])
        self.assertEqual(result["documents"]["briefing"]["meta"]["authors"]["1"][1], 2)
        bundle["documents"]["briefing"]["meta"]["epoch"] = 1700000000000
        self.error(422, lambda: apply_operations(bundle, [{"op": "add_node", "document": "briefing", "type": "content"}]))

    def test_link_removal_and_document_replacement(self):
        story, original = self.fresh()
        proposal = self.propose(story, [{"op": "remove_link", "document": "briefing", "node": "10"},
                                      {"op": "update_node", "document": "briefing", "node": "10", "data": {"target": 7}}])
        self.assertEqual(proposal["status"], "ready")
        self.apply(story, proposal)
        replacement = copy.deepcopy(original["documents"]["followup"])
        replacement["resources"]["nodes"]["2"]["data"]["monolog"] = "A replacement chapter begins."
        change = self.propose(story, [{"op": "replace_document", "document": "followup", "value": replacement}], 2)
        self.assertEqual(change["status"], "ready")
        self.assertEqual(self.apply(story, change)["revision"], 3)

    def test_client_does_not_forward_bearer_tokens_on_redirect(self):
        received = []
        class Destination(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(True)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *args):
                pass
        destination = HTTPServer(("127.0.0.1", 0), Destination)
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:%s/" % destination.server_port)
                self.end_headers()
            def log_message(self, *args):
                pass
        redirect = HTTPServer(("127.0.0.1", 0), Redirect)
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (destination, redirect)]
        for thread in threads:
            thread.start()
        try:
            self.error(302, lambda: Client("http://127.0.0.1:%s" % redirect.server_port, self.token).request("GET", "/"))
            self.assertEqual(len(received), 0)
        finally:
            for server in (destination, redirect):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join()

    def test_existing_unknown_database_is_preserved(self):
        database = self.work / (uuid.uuid4().hex + ".sqlite3")
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.execute("INSERT INTO unrelated VALUES ('preserve me')")
        original_bytes = database.read_bytes()
        with self.assertRaises(RuntimeError):
            Store(database, self.validator)
        self.assertEqual(database.read_bytes(), original_bytes)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("SELECT value FROM unrelated").fetchone()[0], "preserve me")
            self.assertFalse(connection.execute("SELECT name FROM sqlite_master WHERE name='stories'").fetchall())

    def test_access_assertion_is_ignored_without_access_configuration(self):
        request = urllib.request.Request(self.url + "/v1/session", headers={
            "Authorization": "Bearer " + self.token, "Cf-Access-Jwt-Assertion": "not.a.token"})
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(parse_json(response.read()), {"actor": "bearer-token", "via": "bearer-token"})

    def test_version_1_database_migrates_and_keeps_history(self):
        database = self.work / (uuid.uuid4().hex + ".sqlite3")
        with sqlite3.connect(database) as connection:
            connection.executescript('''
                CREATE TABLE stories (id TEXT PRIMARY KEY, head INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE revisions (story_id TEXT NOT NULL REFERENCES stories(id), number INTEGER NOT NULL,
                    bundle TEXT NOT NULL, sha256 TEXT NOT NULL, validation TEXT NOT NULL, created_at TEXT NOT NULL,
                    message TEXT NOT NULL, proposal_id TEXT, PRIMARY KEY(story_id, number));
                CREATE TABLE proposals (id TEXT PRIMARY KEY, story_id TEXT NOT NULL REFERENCES stories(id),
                    base_revision INTEGER NOT NULL, candidate TEXT NOT NULL, sha256 TEXT NOT NULL, validation TEXT NOT NULL,
                    diff TEXT NOT NULL, operations TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
                    applied_revision INTEGER);
                CREATE INDEX proposal_story ON proposals(story_id);
                PRAGMA user_version=1;''')
            connection.execute("INSERT INTO stories VALUES ('legacy', 1, '2026-10-01T00:00:00+00:00')")
            connection.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?)", ("legacy", 1, json.dumps(self.fixture), content_hash(self.fixture),
                               '{"ok": true, "errors": []}', "2026-10-01T00:00:00+00:00", "Imported before identities", None))
        store = Store(database, self.validator)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
        legacy = store.read("legacy")
        self.assertEqual((legacy["message"], legacy["committed_by"]), ("Imported before identities", None))
        proposal = store.propose("legacy", 1, [self.edit()], "First recorded edit", "writer@example.com")
        self.assertEqual(store.apply("legacy", proposal["id"], "reviewer@example.com")["committed_by"], "reviewer@example.com")
        self.assertEqual([item["committed_by"] for item in store.history("legacy")], [None, "reviewer@example.com"])
        Store(database, self.validator)  # Reopening an already migrated database changes nothing.

    def test_client_sends_access_service_token_headers(self):
        received = []
        class Capture(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append({key: self.headers.get(key) for key in ("CF-Access-Client-Id", "CF-Access-Client-Secret")})
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Capture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = "http://127.0.0.1:%s" % server.server_port
            with mock.patch.dict(os.environ, {"ARROW_STORY_ACCESS_CLIENT_ID": "abc123.access", "ARROW_STORY_ACCESS_CLIENT_SECRET": "s3cret"}):
                Client(url, self.token).request("GET", "/")
            Client(url, self.token).request("GET", "/")
            self.assertEqual(received, [{"CF-Access-Client-Id": "abc123.access", "CF-Access-Client-Secret": "s3cret"},
                                        {"CF-Access-Client-Id": None, "CF-Access-Client-Secret": None}])
            with mock.patch.dict(os.environ, {"ARROW_STORY_ACCESS_CLIENT_ID": "abc123.access"}):
                with self.assertRaises(ValueError):
                    Client(url, self.token)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


TEAM = "https://arrow-test.cloudflareaccess.com"
AUDIENCE = "4714c1358e65fe4b408ad6d432a5f878f08194bdb4752441fd56faefa9b2b6f2"


class AcceptingValidator:
    def validate(self, candidate):
        return {"ok": True, "errors": [], "fingerprint": "test"}


class StaticKeys:
    """Stands in for Cloudflare's published signing keys."""
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, assertion):
        return self


class AccessTests(unittest.TestCase):
    """Cloudflare Access identities with an in-process service and locally signed assertions."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="arrow-access-tests-")
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.token = secrets.token_urlsafe(32)
        cls.fixture = load_bundle(ROOT / "examples/dialogic_bridge/story/guild_story.json")
        store = Store(Path(cls.temporary.name) / "access.sqlite3", AcceptingValidator())
        app = create_app(store=store, token=cls.token, access=AccessVerifier(TEAM, AUDIENCE, keys=StaticKeys(cls.private.public_key())))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}"
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 15
        while not cls.server.started:
            if time.monotonic() > deadline or not cls.thread.is_alive():
                raise RuntimeError("Access test service did not start")
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.temporary.cleanup()

    def assertion(self, key=None, algorithm="RS256", **claims):
        issued = int(time.time())
        values = {"aud": [AUDIENCE], "iss": TEAM, "iat": issued, "exp": issued + 600, "email": "writer@example.com"} | claims
        return jwt.encode({name: value for name, value in values.items() if value is not None}, key or self.private, algorithm=algorithm)

    def call(self, method, path, value=None, assertion=None, bearer=None):
        headers = {"Content-Type": "application/json"}
        if assertion:
            headers["Cf-Access-Jwt-Assertion"] = assertion
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        request = urllib.request.Request(self.url + path, method=method, headers=headers,
                                         data=None if value is None else json.dumps(value).encode())
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, parse_json(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, parse_json(error.read())

    def import_story(self, assertion):
        bundle = copy.deepcopy(self.fixture)
        bundle["package"]["id"] = "access_" + uuid.uuid4().hex[:12]
        status, created = self.call("POST", "/v1/stories", {"bundle": bundle, "message": "Import"}, assertion=assertion)
        self.assertEqual(status, 201, created)
        return created

    def test_signed_in_identities_are_recorded(self):
        writer, reviewer = self.assertion(), self.assertion(email="reviewer@example.com")
        self.assertEqual(self.call("GET", "/v1/session", assertion=writer), (200, {"actor": "writer@example.com", "via": "cloudflare-access"}))
        created = self.import_story(writer)
        story = created["story_id"]
        self.assertEqual(created["committed_by"], "writer@example.com")
        edit = {"op": "update_node", "document": "briefing", "node": "2", "data": {"content": "Signed edit."}}
        status, proposal = self.call("POST", f"/v1/stories/{story}/proposals",
                                     {"base_revision": 1, "operations": [edit], "message": "Signed proposal"}, assertion=writer)
        self.assertEqual((status, proposal["created_by"], proposal["applied_by"]), (201, "writer@example.com", None))
        status, applied = self.call("POST", f"/v1/stories/{story}/proposals/{proposal['id']}/apply", {}, assertion=reviewer)
        self.assertEqual((status, applied["revision"], applied["committed_by"]), (200, 2, "reviewer@example.com"))
        # A repeated apply by someone else keeps the original record.
        self.call("POST", f"/v1/stories/{story}/proposals/{proposal['id']}/apply", {}, assertion=writer)
        _, listed = self.call("GET", f"/v1/stories/{story}/proposals", assertion=writer)
        self.assertEqual([(item["created_by"], item["applied_by"]) for item in listed["proposals"]], [("writer@example.com", "reviewer@example.com")])
        _, history = self.call("GET", f"/v1/stories/{story}/revisions", assertion=writer)
        self.assertEqual([item["committed_by"] for item in history["revisions"]], ["writer@example.com", "reviewer@example.com"])

    def test_invalid_assertions_are_rejected(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        issued = int(time.time())
        for name, assertion in {
            "expired": self.assertion(iat=issued - 7200, exp=issued - 3600),
            "other audience": self.assertion(aud=["0" * 64]),
            "other team": self.assertion(iss="https://other.cloudflareaccess.com"),
            "other signing key": self.assertion(key=other_key),
            "symmetric algorithm": self.assertion(key="x" * 64, algorithm="HS256"),
            "no expiry": self.assertion(exp=None),
            "no identity": self.assertion(email=None),
            "malformed": "not.a.token",
        }.items():
            with self.subTest(name):
                status, value = self.call("GET", "/v1/session", assertion=assertion)
                self.assertEqual((status, value["error"]["code"]), (401, "unauthorized"))
        # Behind Access the assertion decides; a valid bearer token does not rescue an invalid sign-in.
        status, _ = self.call("GET", "/v1/session", assertion=self.assertion(aud=["0" * 64]), bearer=self.token)
        self.assertEqual(status, 401)

    def test_bearer_token_and_service_tokens(self):
        self.assertEqual(self.call("GET", "/v1/session", bearer=self.token), (200, {"actor": "bearer-token", "via": "bearer-token"}))
        self.assertEqual(self.call("GET", "/v1/session")[0], 401)
        service = self.assertion(email=None, common_name="abc123.access")
        self.assertEqual(self.call("GET", "/v1/session", assertion=service)[1]["actor"], "service-token:abc123.access")
        self.assertEqual(self.import_story(service)["committed_by"], "service-token:abc123.access")

    def test_access_configuration_is_validated(self):
        for team, audience in (("http://arrow-test.cloudflareaccess.com", AUDIENCE), (TEAM + "/path", AUDIENCE),
                               (TEAM, "not an aud tag"), (TEAM, "")):
            with self.subTest(team=team, audience=audience), self.assertRaises(RuntimeError):
                AccessVerifier(team, audience, keys=StaticKeys(None))
        with mock.patch.dict(os.environ, {"ARROW_STORY_ACCESS_TEAM_DOMAIN": TEAM}, clear=False):
            os.environ.pop("ARROW_STORY_ACCESS_AUD", None)
            with self.assertRaises(RuntimeError):
                cloudflare_access.from_environment()
        with mock.patch.dict(os.environ, {"ARROW_STORY_ACCESS_TEAM_DOMAIN": "", "ARROW_STORY_ACCESS_AUD": ""}):
            self.assertIsNone(cloudflare_access.from_environment())


if __name__ == "__main__":
    unittest.main()
