# Arrow editor and MCP connector — v0.2

The desktop Arrow editor and MCP adapter both connect to the shared HTTP story
service. They use its existing revisions, compiler validation, proposals, review
diffs and explicit apply endpoint. Neither client has direct database access.
Godot still owns dialogue playback and runtime state.

## Arrow desktop UI

Start the service using [story-service.md](story-service.md), then run this Arrow
fork with Godot. Open **App menu → Story service…**. Set the service URL and
bearer token, then **Connect**. The window also reads `ARROW_STORY_URL` and
`ARROW_STORY_API_TOKEN` from Arrow's launch environment. Tokens stay in memory;
they are never written into Arrow projects, configuration or binding metadata.
Remote URLs require HTTPS; HTTP is accepted only on loopback. Redirects are
refused. The native connector does not need Python inside the editor.

For a new story, enter a review message and choose **Import manifest…**, selecting
an existing `arrow-story` package `.json` manifest. The service validates the
whole package before creating revision 1. Chapter paths must stay beneath the
manifest directory and cannot contain symlinks. The non-canon example is
`examples/dialogic_bridge/story/guild_story.json`.

For an existing story:

1. Select it and choose **Load story**.
2. Select a chapter and choose **Open chapter in Arrow**. Save or close any
   unsaved current project first. Arrow creates a separate local project and
   opens its graph; existing local projects are preserved.
3. Edit the chapter with Arrow's graph and inspectors. Return to **Story
   service…**, enter a review message, and choose **Propose current Arrow chapter**.
   This snapshots the editable in-memory chapter, including unsaved graph edits.
   Apply any pending inspector changes using Arrow's normal Update action first.
4. Read the proposal's before/after changes, compiler diagnostics and status. **Apply reviewed
   proposal** commits a ready proposal after the service rechecks its revision
   and runs the current compiler. Invalid/stale proposals cannot commit.
5. After applying, load the story again and open a fresh local chapter to edit
   the new revision. Local saves continue to save only to Arrow's local storage.

The proposal list includes edits from MCP and the CLI. **Refresh proposals**
starts at the first page; **Next page** advances through batches of 50.
Double-click a proposal or select **Review selected** to fetch its current
status, exact before/after diff and compiler diagnostics. Story text is displayed
as plain text, without executing markup. Selecting another proposal or changing
connection credentials clears the reviewed selection.

Every service-opened local project retains its original service, chapter and base
revision in `user://story-service-bindings.json`, without credentials. Bindings
survive editor restarts; reopening that local project from Arrow's usual project
list allows another proposal against the original base. Refreshing the service
never silently rebases local work. A stale proposal attempt returns a conflict
and leaves local changes intact. Open a fresh chapter and deliberately copy the
wanted edits when resolving a conflict. There is no automatic merge or live sync.

The chapter editor proposes one full chapter replacement at a time. Shared story
settings have their own structured batch editor below. The existing supported
compiler subset still applies. Service UI is desktop only; the browser Arrow
build does not expose this menu item.

## Shared story settings

Load a story, then choose **Shared story settings…** beside **Open chapter in
Arrow**. This window edits the loaded revision without changing the open local
chapter or the shared story until a proposal is applied.

- **Chapters** shows the story's start node, chapter titles and source paths.
- **Shared characters** changes an existing character's display name in the
  registry and every mapped chapter together. The window lists affected chapters.
- **Shared variables** changes an existing authored default across all mappings.
  True/false uses a checkbox; text uses a text field; numbers use a whole-number
  field bounded to ±9007199254740991, matching the native compiler. Defaults do
  not change running game state or existing saves.
- **Chapter links** selects an existing Jump, destination chapter and node.
  **Stage chapter link** pairs the Jump's external-target placeholder with its
  package link in one batch. **Stage local jump** removes any package link and
  sets the selected local destination in the same batch. Creating new Jump nodes
  still happens in Arrow's graph editor.
- **Staged changes** lists the batch and supports removing individual changes or
  clearing it. Staging a different value for the same definition/Jump replaces
  that staged change. Returning to the loaded value removes it. Each batch is
  bounded to the service's 100-operation limit; a Jump conversion uses two.

Enter a review message and choose **Propose staged changes**. The service validates
all edits together using the existing structured operation API. Its exact diff,
compiler diagnostics and ready/invalid status appear in the service review
window, where the normal **Apply reviewed proposal** action commits them. MCP and
the CLI can read and apply the same proposal. Creating a proposal does not change
the authoritative revision.

A batch keeps its original service and base revision. Loading newer sources in
the parent window does not rebase pending edits; trying to submit a stale batch
returns a conflict and retains it for deliberate resolution. Switching servers
cannot silently send a batch to a different service. A failed request retains
staged edits. A successful proposal stores them immutably on the service and
clears the local batch. Close/reopen preserves staged edits within the current
Arrow session; **unsubmitted batches are not persisted across application
restarts**. Propose work you want to retain before quitting.

After applying shared settings, load the story and open a fresh chapter before
continuing graph edits. Existing local chapters retain their old base revision
and do not update automatically. Chapter inventory, new registry entries,
resource mappings and the story entry point still use the package JSON; adding
or removing chapters/definitions is outside this editor's scope.

## MCP installation and launch

Use Python 3.11+ from the repository root. The optional MCP environment includes
the service dependencies and the official Python MCP SDK **1.30.0**:

```sh
python3 -m venv /tmp/arrow-mcp-venv
/tmp/arrow-mcp-venv/bin/python -m pip install -r services/story_mcp/requirements.lock
```

`requirements.txt` declares direct requirements; `requirements.lock` pins the
tested full dependency set. Keep the HTTP service running separately, with Godot
installed there. The MCP process only needs Python and network access to that
service. Supply the same `ARROW_STORY_API_TOKEN` through the MCP host's private
process environment, and optionally set `ARROW_STORY_URL`:

```sh
cd /path/to/Arrow
/tmp/arrow-mcp-venv/bin/python -m services.story_mcp --url http://127.0.0.1:8765
```

This is an **MCP stdio server**. An MCP host starts the command and exchanges
JSON-RPC through stdin/stdout; service logs go to stderr. Running it by hand waits
for MCP input. It does not start the HTTP backend. No token belongs in command
arguments, checked-in host configuration, or chat messages.

A generic host entry, after configuring the token in that host's environment:

```json
{
  "mcpServers": {
    "arrow-story": {
      "command": "/absolute/path/to/arrow-mcp-venv/bin/python",
      "args": ["-m", "services.story_mcp", "--url", "http://127.0.0.1:8765"],
      "cwd": "/absolute/path/to/Arrow"
    }
  }
}
```

Host configuration formats differ. If the host has no `cwd` option, use its
working-directory setting or set `PYTHONPATH=/absolute/path/to/Arrow` in the
host's private environment. This is a local stdio adapter, not a hosted remote
ChatGPT connector. No OAuth, remote MCP HTTP transport, public deployment or
account installation is included.

## MCP tools

| Tool | Effect |
| --- | --- |
| `list_stories` | Read story IDs and head revisions. |
| `read_story(story_id, revision?)` | Read full sources and validation at head or a historical revision. |
| `list_story_revisions(story_id, after=0, limit=50)` | Page immutable revision metadata. |
| `list_story_proposals(story_id, after=0, limit=50)` | Page proposals; use returned `sequence` as the next cursor. |
| `read_story_proposal(story_id, proposal_id, include_candidate=false)` | Read operations, diff, validation, current head and status; optionally include full candidate. |
| `propose_story_edits(story_id, base_revision, operations, message)` | Save a validated candidate for review; does not advance the head. |
| `apply_story_proposal(story_id, proposal_id)` | Explicit commit; exposed only with `--allow-apply`. |

By default the MCP host can read and propose edits; the Arrow UI or existing CLI
applies reviewed proposals. Add `--allow-apply` to the MCP command when that host
should also offer commits. Its tool description asks agents to review the diff
and obtain user authorization; the host controls tool permissions. Tool
annotations are hints, not an authorization mechanism. All clients currently
share the same service token and backend permissions.

`arrow://story-contract` provides the full source and operation contract as an
MCP resource. Tool schemas expose the supported operations as a discriminated
union, including registry updates, graph connections, external links, document
replacement and restoration. Unknown operations/extra fields are rejected. Node
UIDs are decimal **strings**, not integers. Arrow allocates new resource IDs.
Narrative content is source data and must not be treated as agent instructions.

An example `propose_story_edits` call after reading revision 1:

```json
{
  "story_id": "jade_lantern_demo",
  "base_revision": 1,
  "operations": [
    {"op": "update_node", "document": "briefing", "node": "2",
     "data": {"content": "A revised request waits on the guild board."}}
  ],
  "message": "Clarify the opening request"
}
```

Review the returned diff and diagnostics. The proposal result omits the full
candidate to avoid duplicating all sources; `read_story_proposal` can include it.
Service errors are MCP tool errors containing structured JSON with the HTTP
status, code and conflict/validation details. Read fresh sources after a
`revision_conflict` and deliberately revise the proposal; the adapter never
changes its base revision automatically. Apply retries retain the service's
idempotent behavior. Restoration creates a new revision with old content.

## Validation

```sh
ARROW_STORY_GODOT=/path/to/godot /tmp/arrow-mcp-venv/bin/python \
  -m unittest discover -s tests/connectors -v
```

The connector suite launches real MCP stdio processes and the HTTP backend with
isolated SQLite storage and native Godot compilation. It checks discovery,
schemas, resources, authentication, review/apply/restore/history, registry types,
invalid/stale edits, and an MCP → Arrow → MCP editing round trip. Arrow runs in
an isolated copy and user directory; the suite opens and renders graph nodes,
checks restart bindings, and scans persisted files for accidental token storage.
It also tests shared names/defaults across differently mapped chapter IDs, all
three supported default types, whole-number bounds, atomic external/local Jump
conversion, frozen staged batches, cross-service refusal, conflicts, and empty
registries/chapters without Jumps.
Set `ARROW_CONNECTOR_ARTIFACTS` to a new directory to retain logs; add
`ARROW_CONNECTOR_VISUAL=1` with an available display for a review screenshot.

The existing service and bridge tests remain separate. These checks validate the
local clients and protocol, not a specific third-party host installation or the
absent Jade Lantern game adapter.
