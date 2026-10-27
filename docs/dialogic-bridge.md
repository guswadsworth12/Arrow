# Arrow to Dialogic bridge — v0.2

This hardens the first integration milestone for this Arrow fork. It adds a native
Godot EditorPlugin, a strict compiler, a non-canon example, and isolated tests.
It does not yet add AI editing, hosted storage, or cross-document authoring.

## Install in a game

1. Install and enable **Dialogic 2.0 Alpha 20** in your Godot 4.5+ project.
   The reference release is `dialogic-godot/dialogic@2.0-alpha-20`, commit
   `e301e1bb5e62e6de22c6b7748c4372d0570bb4a7`.
2. Copy **only** `addons/arrow_dialogic` from this repository into your game's
   `addons/` directory. Enable **Arrow to Dialogic** in Project Settings → Plugins.
3. Copy an Arrow `.arrow` document or JSON export into your game project.
4. Choose **Project → Tools → Import Arrow into Dialogic…**, then select it.
5. The result dialog gives the generated `story.dtl` path and a
   `Dialogic.start("res://generated/arrow/…/story.dtl")` call.

The addon has no Python dependency. Python is used only by the test harness.
Keep the upstream MIT licenses with distributed code. Dialogic is not vendored.

## What importing does

The compiler flattens the scenes **inside one Arrow document** into one timeline,
using labels derived from Arrow node IDs. This preserves branches that reconnect
and jumps between those scenes without duplicating their text. Every block ends
in an explicit jump or `end_timeline`; physical file order cannot leak execution
into another branch.

Generated output goes under a per-source directory in `res://generated/arrow/`:

- `story.dtl`: generated timeline, replaced on import.
- `manifest.json`: source checksum, ID mapping, bindings and variable defaults.
- `arrow_character_<source-hash>_<UID>.dch`: character stubs, created only if absent. Add portraits
  and styling to these in Dialogic; reimport preserves existing stubs.

Missing Dialogic variable defaults are added to `project.godot`; existing defaults
are preserved. Generated timelines **never initialize variables on playback**.
Your game must own save/load and initialize a new game's state deliberately.
Importing during a running game does not migrate live state.

Existing characters can be bound by adding a Dictionary project setting named
`arrow_dialogic/character_bindings`, mapping string Arrow character UIDs to
existing Dialogic character identifiers, for example `{"100": "Rend"}`. Register
those characters in Dialogic first. A bound character gets no generated stub.

Keep Arrow as the authored source. Editing a generated `.dtl` is temporary:
reimport replaces it. There is no reverse import in this milestone.

Imports stage the timeline, manifest, new character stubs, and project settings
before replacing any published files. Handled write/rename failures roll back the
whole file set and leave in-memory defaults and resource registrations unchanged.
If rollback itself fails, the error identifies retained backup paths for recovery.
Validation failures never publish output. The manifest must belong to the selected
source before an existing output directory can be replaced.

This is recovery from ordinary I/O failures, **not** a crash-safe filesystem
transaction. A process crash or power loss during replacement may require restoring
`.arrow-backup-*` files. Use one editor/importer at a time, and keep authored files
under version control. Existing character stubs are checked as Dialogic resources;
identifier collisions and stale/unknown explicit bindings stop the import.

Generated characters and timelines are registered explicitly by stable identifiers;
character display names do not determine their identity. Existing integer and
floating-point numeric defaults are treated as the same numeric type. Other
conflicting types stop import. No runtime state or game autoload is modified.

## Supported subset

| Arrow node | Mapping / constraint |
| --- | --- |
| Entry | Stable timeline label and continuation |
| Content | Title, then narration; non-default brief/auto/clear flags rejected |
| Monolog | Character speech; non-default brief/auto/clear flags rejected |
| Playable Dialog | Single-line choices and their outgoing branches |
| Non-playable Dialog | Exactly one spoken line; randomized alternatives rejected |
| Condition | Boolean equality/inequality; numeric comparisons |
| Variable Update | Assignment only; literal or same-type variable operand |
| Jump | Any existing node in the same document, including another scene |

Arrow's condition slots are **false = 0, true = 1**. Referring to the same variable
on the right-hand side means its **initial value** in Arrow; the compiler preserves
that meaning rather than substituting its current runtime value.

Boolean, string and integer-valued numeric variable defaults are supported. Names
must be ASCII identifiers (letters, digits, underscores, not starting with a
digit). Numeric references and values must be exact integers within ±(2^53−1);
fractional, overflowing, and coercible string/boolean references are rejected. An unwired output ends that branch. Multiline narration becomes separate
text events. Player choices are displayed as options, not voiced speech events.

Explicitly unsupported: other node types (macros, tags, randomizers, generators,
sequencers, hubs, interactions, user input, markers and frames), skipped nodes,
markup/interpolation or angle-bracket random text syntax in prose, backslashes, string comparisons, arithmetic updates,
nonzero target input slots, and independent chapter-file linking. Even unsupported
unreachable nodes fail validation, rather than silently disappearing.

Character color/tags, voice, portrait commands and Arrow console presentation are
not translated. Character stubs carry a display name only. Custom game events,
quest actions and game-state binding are later milestones, not implemented here.

The compiler translates graph cycles but does not prove termination. Dialogic
jumps have their own runtime semantics; test long-running loops before using them
in production. Runtime performance and your game's UI still need game-level QA.

## Example

`examples/dialogic_bridge/guild_briefing.arrow` is a deliberately non-canon test:
accept or ask about a request, change a boolean, reconnect the branches, check that
boolean, then jump to another scene. The flag persists between runs; reset it
explicitly when testing the unaccepted path or start a new game.

## Tests

```sh
python3 tests/run_bridge_tests.py --godot /path/to/godot
python3 tests/run_bridge_tests.py --godot /path/to/godot --dialogic /path/to/dialogic-checkout
```

Use the pinned Alpha 20 checkout for the second command. The harness copies files
into an isolated temporary project with separate Godot user data, configuration,
and caches. When `--dialogic` is supplied it verifies the exact pinned checkout
commit before testing. It does not verify uncommitted edits to that checkout. `--work-dir /tmp/arrow-bridge-test` keeps a fresh `run-*` directory containing
separate numbered logs and imported resources for troubleshooting. Existing runs
are never overwritten. A required success marker, process exit status, and absence
of Godot/script errors must all agree before a test stage passes. It checks Godot script errors as well
as process exit status. Compiler tests verify wiring, deterministic output,
unsupported behavior, bad references, typed operands and prose escaping. The
Dialogic test uses its actual event parser and variable/expression subsystems.
An editor smoke test runs the real importer, loads the generated character,
reimports without resetting defaults, and checks that invalid input preserves
the existing generated timeline.

Additional checks:

```sh
# Open both example scenes, draw every graph node, and load every inspector.
python3 tests/run_bridge_tests.py --godot godot --arrow

# Exercise actual Dialogic playback; --visual requires an X11/display session.
# Captures choice controls and branch outcomes in the retained run directory.
python3 tests/run_bridge_tests.py --godot godot --dialogic /path/to/dialogic-checkout \
  --arrow --visual --work-dir /tmp/arrow-bridge-visual
```

The Arrow check imports a separate copy of the editor so it cannot rewrite the
checkout's tracked import metadata. Compiler regressions cover malformed JSON
fields, exact numeric references, unsupported syntax and stable graph semantics.
Publisher tests inject each backup/replace failure, including project settings.
The real importer checks resolved character resources, numeric/default preservation,
rollback of files and memory, reimport, and invalid-import preservation. Playback
uses real Dialogic layouts and choice buttons, follows both outcomes into the
second scene, and asks again after acceptance to verify state persists.

Verified locally with **Godot 4.6.3** and the pinned Dialogic Alpha 20 source:
compiler/publication regressions; real parser/subsystems; editor import and rollback;
rendered choice and branch playback; and the example in Arrow. These visual checks
use a minimal project and the default Dialogic style, not Jade Lantern's game UI.
The earlier v0.1 compiler was tested on Godot 4.5.1; the v0.2 changes have not been
rechecked on that version. CI targets Godot 4.6 with an official SHA-512 checksum;
GitHub Actions has not been observed running. No game project is present in this
workspace, so no existing game conversation or adapter has been changed.

## Next milestones

1. Verify the addon UI and both branches in the actual game; map quest/game state through a small adapter without changing the playable demo.
2. Add supported node mappings and asset bindings as real scenes require them.
3. Shared character/variable registry and cross-document dependency tracking.
4. Hosted persistence with revision checks; structured MCP editing operations.
5. AI change review and a ChatGPT interface over the same authoritative project.
