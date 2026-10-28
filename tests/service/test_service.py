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
import urllib.error
import urllib.request
import uuid

from services.story_service.client import Client, checkout, load_bundle
from services.story_service.model import ServiceError, apply_operations, bundle_shape, changes, content_hash, parse_json, source_text
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


if __name__ == "__main__":
    unittest.main()
