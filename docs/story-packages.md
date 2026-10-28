# Linked story packages — v1

A story package links several ordinary Arrow v3 documents into one Dialogic
2.0-alpha-20 timeline. The package manifest is the explicit shared character and
variable registry, chapter inventory, entry point, and cross-document link table.
It is a local, version-controlled file contract; no hosted service is involved.

Start with [`guild_story.json`](../examples/dialogic_bridge/story/guild_story.json).
Both accompanying `.arrow` files are non-canon examples and open independently in
Arrow. Copy the manifest and both files into the Godot project, preserving their
relative paths, then choose **Project → Tools → Import Arrow into Dialogic…** and
select the manifest. The addon validates every chapter before publishing anything.

## Authoring contract

The manifest uses `"format": "arrow-story"`, `"version": 1`, an ASCII identifier
`id`, a `title`, and these fields:

| Field | Meaning |
| --- | --- |
| `registry.characters` | Stable character keys with display `name` strings. |
| `registry.variables` | Stable variable keys with `name`, `type`, and `init`. Types/defaults follow the single-document compiler's supported subset. |
| `documents` | Stable chapter keys with relative `path` and explicit `characters`/`variables` local-UID-to-registry-key maps. |
| `entry` | `{ "document": "briefing", "node": "1" }`; the node must be an Entry. |
| `links` | An array of `{ "from": { "document": "briefing", "node": "10" }, "to": { "document": "followup", "node": "1" } }` records. |

Example mappings for the same resources in two files:

```json
{
  "registry": {
    "characters": {"rend": {"name": "Rend"}},
    "variables": {
      "request_accepted": {"name": "request_accepted", "type": "bool", "init": false}
    }
  },
  "documents": {
    "briefing": {
      "path": "briefing.arrow",
      "characters": {"100": "rend"},
      "variables": {"200": "request_accepted"}
    },
    "followup": {
      "path": "followup.arrow",
      "characters": {"900": "rend"},
      "variables": {"901": "request_accepted"}
    }
  }
}
```

This excerpt needs the format, version, id, title, entry and links from the full
example. All local character and variable resources must have mappings; aliases
within one document cannot collapse multiple local resources onto one registry
key. Character names and variable names, types, and initial values must match their
registry definitions. Conflicts stop import with chapter context. Nothing edits
or synchronizes the source `.arrow` files automatically.

Node IDs in manifest references are canonical nonnegative decimal **strings**.
Each link source must be a Jump with `target: -1`; this marks an external jump
placeholder explicitly rather than overriding a valid local destination. Only
one link may use a source jump. Targets must exist in the named chapter. They can
be any supported node, although Entry nodes are recommended chapter boundaries.
Ordinary local wires/jumps remain local even if another document reuses the same
UID. Cycles and return links compile, but termination is not proved.

Paths are relative to the manifest directory and may use subdirectories. Absolute
paths, `.`/`..` segments, duplicate paths (including case-only differences), and symlink dependencies are rejected.
Every chapter must exist and pass the original strict compiler. Unsupported nodes
in unreachable chapters are still errors. The existing supported node subset is
unchanged.

## Runtime identity and state

The package emits one `story.dtl` under the existing per-source output directory.
Shared registry resources are emitted once; local node/scene IDs are remapped
without relying on their numeric uniqueness across files. Stable timeline labels
use `arrow_<chapter-key>__<local-node-UID>`, for example `arrow_followup__1`.
Changing chapter order or a chapter's filename preserves labels. Renaming a
chapter key or node UID changes its labels. Internal remapping detects ID
collisions and stops import.

Generated `manifest.json` format **2** includes `story_id`, `registry_ids`,
`document_labels`, `chapter_entries`, `document_dependencies`, link records, and
SHA-256 dependency snapshots for the source manifest and every chapter. A
single-document import still uses manifest format 1 and its previous labels.

Use the import result's timeline path to start the package normally:

```gdscript
Dialogic.start(timeline_path)
# Start a chapter directly using the emitted manifest's chapter_entries value:
Dialogic.start(timeline_path, "arrow_followup__1")
```

There is one Dialogic variable per registry `name`. Those names occupy Dialogic's
global variable namespace: separate packages using the same name will share it.
Choose distinct names for unrelated state. Cross-package registry reconciliation
and state migration are not implemented. Within a package, a variable's initial
value is unambiguous across chapters, including Arrow self-reference operands.

Existing Dialogic defaults are preserved on reimport after type checking, and
no generated chapter entry resets variables. The game owns new-game initialization,
quests, save/load, and state migration. Entering a chapter directly therefore sees
the current game state, not a fresh copy of chapter-local defaults.

Generated character stubs are shared by registry identity within the package and
preserved on reimport. For existing game characters, set the Dictionary project
setting `arrow_dialogic/story_character_bindings`, for example:

```gdscript
{"jade_lantern_demo/rend": "existing_Rend"}
```

Keys are `<story-id>/<registry-character-key>`; values are registered Dialogic
character identifiers. The importer checks the actual resource type. Bindings
for other story IDs are left alone. Single-document UID bindings remain in the
separate `arrow_dialogic/character_bindings` setting. Registry keys identify
characters; display names do not determine identity.

The source manifest path still determines the output directory and generated
character identifiers. Moving/renaming that manifest creates a new import identity;
existing generated output and customized stubs are not relocated or deleted.

## Dependency checks and publication

Choose **Project → Tools → Check Arrow import dependencies…** and select the
original manifest (or a single `.arrow` source). The read-only report lists changed
or missing dependencies, or confirms the import is current. Reimport the manifest
after reviewing changes. There is no filesystem watcher or automatic reimport.

Before publication, the importer rechecks the source checksums it compiled. A
missing, invalid, conflicting, or changed chapter cannot partly update the story.
Timeline, manifest, new stubs, and project settings use the same staged publication
and handled-error rollback as single-document imports. This is not a concurrent
editing or crash-safe transaction; one editor/importer must own publication.
Checksums report freshness, not authenticity or a server-side revision lock.

## Validation and remaining work

`tests/run_bridge_tests.py` now includes package validation and real package
import/playback tests. With pinned Dialogic, it verifies both later-chapter
outcomes, one resolved character resource across different local UIDs, persistent
state on replay, and direct chapter entry. Dependency changes, invalid-input
preservation, reimport and publication rollback are tested through the editor.
`--arrow` opens all three example documents and their inspectors in an isolated
Arrow copy; `--visual` renders runtime checks and retains screenshots when paired
with `--work-dir`.

```sh
python3 tests/run_bridge_tests.py --godot godot --dialogic /path/to/pinned-dialogic --arrow
python3 tests/run_bridge_tests.py --godot godot --dialogic /path/to/pinned-dialogic \
  --arrow --visual --work-dir /tmp/arrow-story-review
```

Arrow currently edits each chapter's graph; the registry and cross-chapter link
table are edited as JSON. Arrow's built-in console cannot follow the external
jump placeholders; package playback is validated in Dialogic. An integrated story
registry/link editor, a Jade Lantern game adapter, public hosting and a hosted
ChatGPT interface remain later work. The optional [revision service](story-service.md)
and [Arrow UI/MCP clients](story-ui-mcp.md) now provide shared persistence,
structured editing and proposal review over these same story sources.
