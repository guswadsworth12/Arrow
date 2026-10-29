# Shared story service — v0.1

The optional service provides one authoritative source store for Arrow story
packages. It adds persistent SQLite revisions, structured edit proposals with
review diffs, optimistic conflict detection, and reversible commits. The native
Godot package compiler validates imports and proposals; it is run again before
every new commit. The existing Arrow-to-Dialogic addon remains native GDScript
and does not require this service or Python.

The [Arrow desktop/PWA UI and MCP adapter](story-ui-mcp.md) also connect to this
backend. The CLI remains available for source checkout and structured edits.
Optional [Cloudflare Access sign-in](cloudflare-access.md) identifies who imports,
proposes and applies each change. No hosted ChatGPT connector, public deployment,
game adapter, user roles, or real-time collaborative editor is included.

## Install and run

Use Python **3.11+** (tested on 3.12), SQLite from the Python standard library,
and the same Godot binary used to validate the bridge (tested on **4.6.3**).
From the repository root:

```sh
python3 -m venv /tmp/arrow-story-venv
/tmp/arrow-story-venv/bin/python -m pip install -r services/story_service/requirements.lock
```

`requirements.txt` declares the direct dependencies; `requirements.lock` pins the
complete tested environment. No Dialogic checkout is required on the service:
validation uses only `compiler.gd` and `story.gd`, not game scenes or autoloads.

Set a private bearer token securely in the service and client environments. Do
not commit it or put it in command arguments. For a local disposable development
instance, this creates a token without printing it:

```sh
export ARROW_STORY_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export ARROW_STORY_DB=/tmp/arrow-story-data/stories.sqlite3
export ARROW_STORY_GODOT=/usr/local/bin/godot
/tmp/arrow-story-venv/bin/python -m services.story_service serve
```

The service can also host an exported PWA at `/app/` using `serve --web-root PATH`.
Separate-origin PWAs use the exact `ARROW_STORY_ALLOWED_ORIGINS` JSON allowlist.
See [PWA hosting and exports](pwa.md) for configuration.

The default bind is `127.0.0.1:8765`. Every `/v1/` operation requires
`Authorization: Bearer <token>`. Startup rejects missing tokens and tokens shorter
than 32 printable ASCII characters. `GET /health` is public liveness only; an
actual validated import demonstrates compiler and database readiness. Browser UI is served only when an exported PWA root is configured. Use `--host`, `--port`, or `--workers` after `serve` to configure the
ASGI process. Run commands from the repository root so the source modules and
native addon can be located.

For self-hosting, run the service under your process supervisor with a persistent
local disk for `ARROW_STORY_DB`, and put an HTTPS reverse proxy in front of its
loopback port. Supply the token through the supervisor's secret environment.
The CLI permits plain HTTP only for loopback hosts and refuses redirects so it
cannot forward a bearer token to another destination. A single shared token
means all authorized clients have the same permissions; this is a private
workspace service, not a multi-tenant public application. To give people their
own sign-in and record who made each change, put it behind
[Cloudflare Access](cloudflare-access.md).

Live processes and environment values do not survive an environment snapshot by
assumption; restart the service with those settings when needed. No external
hosting configuration was deployed during development.

## Import, edit, review, apply

The service currently stores **story packages**, including one-chapter packages.
Standalone `.arrow` files need a package manifest first. Import the non-canon
example:

```sh
/tmp/arrow-story-venv/bin/python -m services.story_service import \
  examples/dialogic_bridge/story/guild_story.json --message 'Import non-canon example'
/tmp/arrow-story-venv/bin/python -m services.story_service checkout \
  jade_lantern_demo /tmp/guild-story-revision-1
```

Each checkout contains ordinary Arrow files and a `.arrow-story-checkout.json`
record containing the service URL, story id, source hash and base revision. It
contains no token. A checkout requires a new directory and never replaces an
existing destination. Open the exported `.arrow` files in Arrow and save changes,
then propose the resulting package:

```sh
/tmp/arrow-story-venv/bin/python -m services.story_service push \
  /tmp/guild-story-revision-1 --message 'Revise the guild briefing'
/tmp/arrow-story-venv/bin/python -m services.story_service proposals jade_lantern_demo
/tmp/arrow-story-venv/bin/python -m services.story_service review jade_lantern_demo PROPOSAL_ID
/tmp/arrow-story-venv/bin/python -m services.story_service apply jade_lantern_demo PROPOSAL_ID
```

`push` only creates a proposal. It does not advance the authoritative revision.
Review its `diff`, `validation` and `status` before calling `apply`. A malformed
operation is rejected. Semantically invalid candidates can be retained as
`invalid` proposals with compiler diagnostics, but cannot commit unless they
pass the current compiler. Proposal inputs are immutable; correcting one means
creating another proposal.

After applying, checkout the new revision into a new directory. Old checkouts
retain their original base revision intentionally. The CLI never silently
advances their revision, merges edits, or replaces their files. Local source
formatting is normalized on export; document IDs, graph data and authorship
metadata are preserved as JSON values. Service edits do not impersonate an Arrow
contributor: resources they create use Arrow's native UIDs under a dedicated
author (see [Creating resources](#creating-resources)).

For a remote service put `--url https://YOUR_SERVICE` **before** the subcommand.
Set the same token securely in that client's environment. The checked-out URL
must match when pushing; a checkout cannot silently switch servers.

## Structured edits

Save a JSON array of operations, then use:

```sh
/tmp/arrow-story-venv/bin/python -m services.story_service propose \
  jade_lantern_demo --base 1 --operations /tmp/story-edits.json \
  --message 'Clarify the request'
```

A minimal `story-edits.json`:

```json
[
  {"op": "update_node", "document": "briefing", "node": "2",
   "data": {"content": "A revised request waits on the guild board."}}
]
```

| Operation | Fields besides `op` | Behavior |
| --- | --- | --- |
| `update_node` | `document`, `node`, optional `name`, optional `data` object | Merge data fields into an existing node; retain other fields. |
| `set_connection` | `document`, `node`, integer `slot`, `target` | Replace that output's wire, targeting a node in the same scene. |
| `disconnect` | `document`, `node`, integer `slot` | Remove an existing output wire. |
| `set_link` | `document`, `node`, `target_document`, `target_node` | Replace an explicit external jump link; package compiler rules still apply. |
| `remove_link` | `document`, `node` | Remove an external link; pair with a valid local jump update in the same batch. |
| `set_variable_default` | `key`, `value` | Update registry `init` and all mapped local definitions together. |
| `set_character_name` | `key`, `value` | Update registry name and all mapped local character definitions together. |
| `replace_document` | `document`, `value` | Replace one chapter with a full Arrow object, such as an editor export. |
| `replace_bundle` | `bundle` | Replace all sources; must be the only operation. Used by checkout push. |
| `restore_revision` | positive integer `revision` | Propose restoring a historical snapshot; must be the only operation. |
| `add_node` | `document`, `type`, optional `scene`, `name`, `data`, `offset`, `ref` | Create a node with a new UID and place it in the scene (the only scene by default). |
| `remove_node` | `document`, `node` | Delete a node, its placement, wires into it and package links from or to it. Entries cannot be removed. |
| `add_variable` | `document`, optional `name`, `type`, `init`, `key`, `ref` | Create a chapter variable mapped to registry `key` (default: `name`). |
| `add_character` | `document`, optional `name`, `color`, `key`, `ref` | Create a chapter character mapped to registry `key` (default: `name`). |

UIDs remain decimal strings. Batches contain 1–100 operations and are validated
as a whole. The service rejects unknown operation fields and preserves the story
id. There is no arbitrary filesystem, shell, SQL or JSON-pointer execution
operation.

### Creating resources

`add_node`, `add_variable` and `add_character` allocate UIDs exactly as Arrow's
native generator does: `(chapter << 43) | (author << 37) | seed`. They use a
dedicated `Arrow Story Service` entry in the chapter's `meta.authors`, created in
the first free author slot, so a human contributor's seed is never advanced.
Allocation skips UIDs already used in the chapter. Chapters that use time-based
Snowflake UIDs (`meta.epoch`) must create resources in Arrow.

Give a created resource a `ref`, then write `"@ref"` in later operations of the
same batch: as `node`, `target` or `target_node`, or as an exact value inside
`data` (for example `"character": "@hesk"`), where it becomes the integer UID.
References are local to their chapter and batch. Read the proposal diff for the
allocated UIDs.

```json
[
  {"op": "add_character", "document": "followup", "name": "Hesk", "ref": "hesk"},
  {"op": "add_node", "document": "followup", "type": "monolog", "ref": "line",
   "data": {"character": "@hesk", "monolog": "The depot is this way."}},
  {"op": "set_connection", "document": "followup", "node": "6", "slot": 0, "target": "@line"}
]
```

New variables and characters always join the shared registry. A new key needs
every field (`name`, `type` and `init` for variables; `name` for characters).
An existing key reuses its registry fields, and a chapter maps each key once.

Registry defaults affect authored initial values and self-reference operands.
They do not migrate game saves, reset live variables, or replace existing game
character customizations. Godot still owns game state, quests and save/load.

## Revisions and conflicts

A proposal names `base_revision`. Both proposal creation and commit check that
revision against the current head. If another writer commits first, the service
returns **409** with the expected/current revisions and commits nothing from the
stale edit. Read the new head and deliberately construct a new proposal; there is
no automatic last-writer-wins update or blind PUT endpoint.

Applying the same proposal again is idempotent: it returns that proposal's
committed revision even if the story has subsequently advanced. Native validation
runs again before any new commit, using the current process's compiler snapshot.
The validation response records Godot's version and a fingerprint of that version,
the native compiler/linker files and validation driver. Restart the service after
updating those files. This is provenance for validation, not a signed attestation.

The revision insert, head update and proposal status update share one SQLite
transaction. A failed write rolls them back together. Restoring revision 1 while
head is 3 creates revision **4** containing the old sources; revisions 2 and 3
remain readable. Server timestamps and caller-supplied messages record the edit
history. Revisions record `committed_by` and proposals `created_by`/`applied_by`:
a verified Cloudflare Access identity, or `bearer-token` for shared-token clients,
which have no per-user identity.

## HTTP contract

JSON bodies use `application/json`. Semantic errors use
`{"error":{"code":"...","message":"..."}}`, with diagnostics or conflict
revisions when applicable. Route/parameter errors may use standard HTTP errors.

| Method and route | Request / result |
| --- | --- |
| `GET /health` | Public process liveness. |
| `GET /v1/session` | `{"actor": ..., "via": "cloudflare-access" \| "bearer-token"}` for the current credentials. |
| `GET /v1/stories` | Story ids, current heads and creation times. |
| `POST /v1/stories` | `{ "bundle": ..., "message": "..." }`; validated revision 1, HTTP 201. Duplicate id is 409. |
| `GET /v1/stories/{id}` | Current revision with sources and validation. |
| `GET /v1/stories/{id}/revisions?after=0&limit=50` | Historical revision metadata, ascending. |
| `GET /v1/stories/{id}/revisions/{n}` | Immutable historical sources and validation. |
| `POST /v1/stories/{id}/proposals` | `{ "base_revision": 1, "operations": [...], "message": "..." }`; reviewed candidate, diff and validation, HTTP 201. |
| `GET /v1/stories/{id}/proposals?after=0&limit=50` | Proposal metadata/status; use returned `sequence` as the next `after` cursor. |
| `GET /v1/stories/{id}/proposals/{proposal}` | Candidate, operations, diff, validation and current status. |
| `POST /v1/stories/{id}/proposals/{proposal}/apply` | `{}`; atomically commit or return the already-applied revision. |

Bundle shape:

```json
{
  "manifest_path": "guild_story.json",
  "package": {"format": "arrow-story", "version": 1, "id": "...", "...": "full manifest"},
  "documents": {"briefing": {"...": "full Arrow document"}, "followup": {"...": "full Arrow document"}}
}
```

The excerpt illustrates the wrapper; use the full sources from the example.
Package/document keys must agree. Paths are confined to staged source files,
with no symlinks, traversal, scripts or collisions with checkout metadata. Story
and chapter keys are ASCII identifiers of at most 64 characters. Limits are
128 chapters, 8 MiB per complete canonical bundle, 8 MiB per HTTP request,
64 JSON nesting levels, and 30 seconds per Godot validation run. Each service
worker allows at most two concurrent validation processes. The validator stages
only trusted compiler scripts plus supplied JSON in an isolated temporary project;
it never loads GDScript supplied by a story or imports the real game project.

## Backups and validation

Keep the SQLite database on local persistent disk. Use SQLite backup rather than
copying only the main file while WAL writes are active:

```sh
/tmp/arrow-story-venv/bin/python -m services.story_service backup \
  /path/to/stories.sqlite3 /path/to/new-backup.sqlite3
```

The destination must not exist. The command includes committed WAL state and
checks backup integrity. To recover, stop the service and configure it to use a
completed backup as its database. Historical revisions and proposal state are
retained. No automatic schema migrations, garbage collection, offsite backup or
multi-host SQLite deployment is implemented.

Run the real HTTP/SQLite/native-Godot suite:

```sh
ARROW_STORY_GODOT=/path/to/godot /tmp/arrow-story-venv/bin/python \
  -m unittest discover -s tests/service -v
python3 tests/run_bridge_tests.py --godot /path/to/godot
```

The service tests use temporary databases, ephemeral bearer tokens and loopback
HTTP. They check concurrent writers, immutable history, proposal validation,
rollback after an injected database failure, replay-safe apply, restoration,
restart durability, backup integrity, Arrow source round-trips, authentication,
request/path limits and malformed data. The existing bridge/package checks remain
separate. Actual public hosting, load testing and the Jade Lantern game are not
validated here. The [Arrow UI and MCP integration guide](story-ui-mcp.md) describes
the clients that use these same revision/proposal operations.
