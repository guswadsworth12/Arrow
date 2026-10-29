"""Append-only story revisions and optimistic commits in SQLite."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .model import (ServiceError, apply_operations, bundle_shape, canonical, changes, content_hash, merge, overlapping,
                    parse_json, reject, whole_numbers)

# Live commits publish before the native compiler finishes; diagnostics follow.
PENDING = {"ok": None, "pending": True, "errors": []}
SUPERSEDED = {"ok": None, "pending": False, "superseded": True, "errors": []}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def revision_number(value) -> int:
    if type(value) is not int or value < 1:
        reject("Revision must be a positive integer")
    return value


def message_text(value) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 512:
        reject("Supply a nonempty review message of at most 512 characters")
    return value.strip()


def actor_name(value) -> str | None:
    if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 320):
        reject("Actor must be a nonempty identity of at most 320 characters")
    return value


class Store:
    def __init__(self, path: str | Path, validator):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self.validator = validator
        self._validating: set[str] = set()
        self._revalidate: set[str] = set()
        self._validation_lock = threading.Lock()
        # Inspect an existing database in one read snapshot before changing even
        # its journal mode. A parallel initializer may commit between snapshots.
        with self.connection() as connection:
            connection.execute("BEGIN")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version not in (0, 1, 2) or (version == 0 and tables) or (version >= 1 and not {"stories", "revisions", "proposals"} <= tables):
                raise RuntimeError("Refusing to change an unknown database schema")
            connection.rollback()
            connection.execute("PRAGMA journal_mode=WAL")
        with self.connection(write=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version not in (0, 1, 2) or (version == 0 and tables):
                raise RuntimeError("Refusing to change an unknown database schema")
            if version == 0:
                schema = '''
                    CREATE TABLE stories (
                        id TEXT PRIMARY KEY, head INTEGER NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE TABLE revisions (
                        story_id TEXT NOT NULL REFERENCES stories(id), number INTEGER NOT NULL,
                        bundle TEXT NOT NULL, sha256 TEXT NOT NULL, validation TEXT NOT NULL,
                        created_at TEXT NOT NULL, message TEXT NOT NULL, proposal_id TEXT,
                        committed_by TEXT,
                        PRIMARY KEY(story_id, number)
                    );
                    CREATE TABLE proposals (
                        id TEXT PRIMARY KEY, story_id TEXT NOT NULL REFERENCES stories(id),
                        base_revision INTEGER NOT NULL, candidate TEXT NOT NULL, sha256 TEXT NOT NULL,
                        validation TEXT NOT NULL, diff TEXT NOT NULL, operations TEXT NOT NULL,
                        message TEXT NOT NULL, created_at TEXT NOT NULL, applied_revision INTEGER,
                        created_by TEXT, applied_by TEXT
                    );
                    CREATE INDEX proposal_story ON proposals(story_id);
                    PRAGMA user_version=2;
                '''
            elif version == 1:
                # Version 2 records who imported, proposed and applied each change; older rows stay NULL.
                schema = '''
                    ALTER TABLE revisions ADD COLUMN committed_by TEXT;
                    ALTER TABLE proposals ADD COLUMN created_by TEXT;
                    ALTER TABLE proposals ADD COLUMN applied_by TEXT;
                    PRAGMA user_version=2;
                '''
            else:
                schema = ""
            for statement in schema.split(";"):
                if statement.strip():
                    connection.execute(statement)

    @contextmanager
    def connection(self, write: bool = False):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _head(connection, story_id) -> int:
        row = connection.execute("SELECT head FROM stories WHERE id=?", (story_id,)).fetchone()
        if row is None:
            raise ServiceError(404, "not_found", "Story was not found")
        return row[0]

    @staticmethod
    def _expected(actual: int, expected: int) -> None:
        if actual != expected:
            raise ServiceError(409, "revision_conflict", "Story changed; read the current revision and propose again",
                               expected_revision=expected, current_revision=actual)

    @staticmethod
    def _revision(row) -> dict:
        if row is None:
            raise ServiceError(404, "not_found", "Revision was not found")
        return {"story_id": row["story_id"], "revision": row["number"], "sha256": row["sha256"],
                "bundle": parse_json(row["bundle"]), "validation": parse_json(row["validation"]),
                "created_at": row["created_at"], "message": row["message"], "proposal_id": row["proposal_id"],
                "committed_by": row["committed_by"]}

    def list_stories(self) -> list:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT id,head,created_at FROM stories ORDER BY id")]

    def read(self, story_id: str, revision: int | None = None) -> dict:
        if revision is not None:
            revision_number(revision)
        with self.connection() as connection:
            # One SQL statement reads head and its snapshot consistently even if a
            # writer commits between requests. Historical revisions are immutable.
            row = connection.execute('''SELECT r.* FROM revisions r JOIN stories s ON r.story_id=s.id
                WHERE s.id=? AND r.number=COALESCE(?,s.head)''', (story_id, revision)).fetchone()
            return self._revision(row)

    def history(self, story_id: str, after: int = 0, limit: int = 50) -> list:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            reject("History pagination requires after>=0 and 1<=limit<=100")
        with self.connection() as connection:
            self._head(connection, story_id)
            return [dict(row) for row in connection.execute('''SELECT number AS revision,sha256,created_at,message,proposal_id,committed_by
                FROM revisions WHERE story_id=? AND number>? ORDER BY number LIMIT ?''', (story_id, after, limit))]

    def create(self, bundle, message, actor: str | None = None) -> dict:
        story_id = bundle_shape(bundle)
        message = message_text(message)
        actor = actor_name(actor)
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM stories WHERE id=?", (story_id,)).fetchone():
                raise ServiceError(409, "already_exists", "Story id is already registered")
        validation = self.validator.validate(bundle)
        if not validation["ok"]:
            raise ServiceError(422, "validation_failed", "Source did not pass the native compiler", errors=validation["errors"])
        text, sha, created = canonical(bundle), content_hash(bundle), now()
        with self.connection(write=True) as connection:
            if connection.execute("SELECT 1 FROM stories WHERE id=?", (story_id,)).fetchone():
                raise ServiceError(409, "already_exists", "Story id is already registered")
            connection.execute("INSERT INTO stories VALUES (?,?,?)", (story_id, 1, created))
            connection.execute('''INSERT INTO revisions (story_id,number,bundle,sha256,validation,created_at,message,proposal_id,committed_by)
                VALUES (?,?,?,?,?,?,?,?,?)''', (story_id, 1, text, sha, canonical(validation), created, message, None, actor))
        return self.read(story_id, 1)

    def propose(self, story_id, base_revision, operations, message, actor: str | None = None) -> dict:
        base_revision = revision_number(base_revision)
        message = message_text(message)
        actor = actor_name(actor)
        base = self.read(story_id)
        self._expected(base["revision"], base_revision)
        if isinstance(operations, list) and len(operations) == 1 and isinstance(operations[0], dict) and operations[0].get("op") == "restore_revision":
            if set(operations[0]) != {"op", "revision"}:
                reject("restore_revision requires exactly op and revision")
            candidate = self.read(story_id, revision_number(operations[0]["revision"]))["bundle"]
        else:
            candidate = apply_operations(base["bundle"], operations)
        diff = changes(base["bundle"], candidate)
        if not diff:
            raise ServiceError(422, "no_changes", "Proposal does not change the story")
        validation = self.validator.validate(candidate)
        proposal_id = uuid.uuid4().hex
        with self.connection(write=True) as connection:
            self._expected(self._head(connection, story_id), base_revision)
            connection.execute('''INSERT INTO proposals (id,story_id,base_revision,candidate,sha256,validation,diff,operations,
                message,created_at,applied_revision,created_by,applied_by) VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,NULL)''',
                               (proposal_id, story_id, base_revision, canonical(candidate), content_hash(candidate),
                                canonical(validation), canonical(diff), canonical(operations), message, now(), actor))
        return self.proposal(story_id, proposal_id)

    def proposals(self, story_id: str, after: int = 0, limit: int = 50) -> list:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            reject("Proposal pagination requires after>=0 and 1<=limit<=100")
        with self.connection() as connection:
            connection.execute("BEGIN")
            head = self._head(connection, story_id)
            rows = connection.execute("SELECT rowid AS sequence,* FROM proposals WHERE story_id=? AND rowid>? ORDER BY rowid LIMIT ?", (story_id, after, limit))
            result = []
            for row in rows:
                validation = parse_json(row["validation"])
                status = ("applied" if row["applied_revision"] is not None else "invalid" if not validation["ok"] else
                          "stale" if row["base_revision"] != head else "ready")
                result.append({key: row[key] for key in ("sequence", "id", "base_revision", "sha256", "message", "created_at", "applied_revision",
                                                         "created_by", "applied_by")}
                              | {"status": status, "validator_fingerprint": validation.get("fingerprint")})
            return result

    def proposal(self, story_id, proposal_id) -> dict:
        with self.connection() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM proposals WHERE id=? AND story_id=?", (proposal_id, story_id)).fetchone()
            if row is None:
                raise ServiceError(404, "not_found", "Proposal was not found")
            result = dict(row)
            for key in ("candidate", "validation", "diff", "operations"):
                result[key] = parse_json(result[key])
            head = self._head(connection, story_id)
        result["current_revision"] = head
        result["status"] = ("applied" if result["applied_revision"] is not None else
                            "invalid" if not result["validation"]["ok"] else
                            "stale" if result["base_revision"] != head else "ready")
        return result

    def apply(self, story_id, proposal_id, actor: str | None = None) -> dict:
        actor = actor_name(actor)
        proposal = self.proposal(story_id, proposal_id)
        if proposal["applied_revision"] is not None:
            return self.read(story_id, proposal["applied_revision"])
        self._expected(proposal["current_revision"], proposal["base_revision"])
        # Revalidate the immutable candidate with the current process's compiler;
        # a proposal's stored validation never authorizes an unchecked commit.
        validation = self.validator.validate(proposal["candidate"])
        if not validation["ok"]:
            raise ServiceError(422, "validation_failed", "Proposal does not pass the current native compiler", errors=validation["errors"])
        with self.connection(write=True) as connection:
            applied = connection.execute("SELECT applied_revision FROM proposals WHERE id=? AND story_id=?", (proposal_id, story_id)).fetchone()[0]
            if applied is not None:
                number = applied
            else:
                self._expected(self._head(connection, story_id), proposal["base_revision"])
                number = proposal["base_revision"] + 1
                connection.execute('''INSERT INTO revisions (story_id,number,bundle,sha256,validation,created_at,message,proposal_id,committed_by)
                    VALUES (?,?,?,?,?,?,?,?,?)''', (story_id, number, canonical(proposal["candidate"]), proposal["sha256"],
                                                   canonical(validation), now(), proposal["message"], proposal_id, actor))
                cursor = connection.execute("UPDATE stories SET head=? WHERE id=? AND head=?", (number, story_id, proposal["base_revision"]))
                if cursor.rowcount != 1:
                    raise ServiceError(409, "revision_conflict", "Concurrent commit prevented publication")
                connection.execute("UPDATE proposals SET applied_revision=?, applied_by=? WHERE id=?", (number, actor, proposal_id))
        return self.read(story_id, number)

    def head(self, story_id: str) -> dict:
        """Head revision metadata and diagnostics, without sources."""
        with self.connection() as connection:
            row = connection.execute('''SELECT r.number,r.sha256,r.validation,r.created_at,r.message,r.committed_by
                FROM revisions r JOIN stories s ON r.story_id=s.id WHERE s.id=? AND r.number=s.head''', (story_id,)).fetchone()
        if row is None:
            raise ServiceError(404, "not_found", "Story was not found")
        validation = parse_json(row["validation"])
        if validation.get("pending"):
            # Covers a restart between a live commit and its validation.
            self._schedule_validation(story_id)
        return {"story_id": story_id, "revision": row["number"], "sha256": row["sha256"], "validation": validation,
                "created_at": row["created_at"], "message": row["message"], "committed_by": row["committed_by"]}

    def changes_between(self, story_id: str, start: int, end: int | None = None) -> dict:
        before = self.read(story_id, start)
        after = self.read(story_id, end)
        return {"story_id": story_id, "from_revision": before["revision"], "to_revision": after["revision"],
                "diff": changes(before["bundle"], after["bundle"])}

    def commit(self, story_id, base_revision, operations, message, actor: str | None = None) -> dict:
        """Publish edits immediately for live editing.

        Edits made against an older revision are rebased onto head: structured
        operations replay on head, and chapter or bundle replacements merge three
        ways. Only edits touching something that changed since their base are
        refused. Compiler diagnostics follow in the background.
        """
        base_revision = revision_number(base_revision)
        message = message_text(message)
        actor = actor_name(actor)
        if not isinstance(operations, list) or any(not isinstance(item, dict) for item in operations):
            reject("Supply a list of structured operations")
        if any(item.get("op") == "restore_revision" for item in operations):
            reject("Restore a revision through a reviewed proposal")
        replaces = any(item.get("op") in ("replace_document", "replace_bundle") for item in operations)
        with self.connection(write=True) as connection:
            number = self._head(connection, story_id)
            if base_revision > number:
                raise ServiceError(409, "revision_conflict", "Base revision does not exist yet",
                                   expected_revision=base_revision, current_revision=number)
            rows = {row["number"]: row for row in connection.execute(
                "SELECT * FROM revisions WHERE story_id=? AND number IN (?,?)", (story_id, base_revision, number))}
            base, head = (whole_numbers(parse_json(rows[key]["bundle"])) for key in (base_revision, number))
            conflicts = []
            if base_revision == number:
                candidate = whole_numbers(apply_operations(base, operations))
            elif replaces:
                candidate, conflicts = merge(base, whole_numbers(apply_operations(base, operations)), head)
            else:
                candidate = whole_numbers(apply_operations(head, operations))
                conflicts = overlapping(changes(head, candidate), changes(base, head))
            if conflicts:
                raise ServiceError(409, "edit_conflict", "Another edit changed the same story data; read again and redo this change",
                                   conflicts=conflicts[:20], base_revision=base_revision, current_revision=number)
            bundle_shape(candidate)
            if candidate["package"]["id"] != story_id:
                reject("A revision cannot rename the story id")
            if not changes(head, candidate):
                return self._revision(rows[number]) | {"changed": False, "rebased_from": None}
            connection.execute('''INSERT INTO revisions (story_id,number,bundle,sha256,validation,created_at,message,proposal_id,committed_by)
                VALUES (?,?,?,?,?,?,?,?,?)''', (story_id, number + 1, canonical(candidate), content_hash(candidate),
                                               canonical(PENDING), now(), message, None, actor))
            connection.execute("UPDATE stories SET head=? WHERE id=?", (number + 1, story_id))
        self._schedule_validation(story_id)
        return self.read(story_id, number + 1) | {"changed": True, "rebased_from": base_revision if base_revision != number else None}

    def _schedule_validation(self, story_id: str) -> None:
        with self._validation_lock:
            if story_id in self._validating:
                self._revalidate.add(story_id)
                return
            self._validating.add(story_id)
        threading.Thread(target=self._validate_head, args=(story_id,), daemon=True).start()

    def _validate_head(self, story_id: str) -> None:
        """Validate the newest live revision; older pending ones are marked superseded."""
        while True:
            try:
                head = self.read(story_id)
                if head["validation"].get("pending"):
                    try:
                        validation = self.validator.validate(head["bundle"])
                    except ServiceError as error:
                        if error.code == "validator_busy":
                            time.sleep(1)
                            continue
                        validation = {"ok": None, "pending": False, "errors": [error.message]}
                    with self.connection(write=True) as connection:
                        connection.execute("UPDATE revisions SET validation=? WHERE story_id=? AND number=?",
                                           (canonical(validation), story_id, head["revision"]))
                        connection.execute("UPDATE revisions SET validation=? WHERE story_id=? AND number<? AND validation=?",
                                           (canonical(SUPERSEDED), story_id, head["revision"], canonical(PENDING)))
            except (ServiceError, sqlite3.Error):
                pass  # The next head read or commit schedules validation again.
            with self._validation_lock:
                if story_id in self._revalidate:
                    self._revalidate.discard(story_id)
                    continue
                self._validating.discard(story_id)
                return

    def backup(self, target: str | Path) -> None:
        target = Path(target)
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with self.connection() as source:
                backup = sqlite3.connect(target)
                try:
                    source.backup(backup)
                    if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Backup integrity check failed")
                finally:
                    backup.close()
        except BaseException:
            target.unlink(missing_ok=True)
            raise
