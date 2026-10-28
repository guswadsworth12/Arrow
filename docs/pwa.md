# Arrow PWA: authoring, shared stories and exports

The Web preset builds an installable Arrow editor with the same shared story
window, chapter graph editing, shared names/defaults, chapter Jump routing and
proposal review/apply workflow as desktop. The PWA downloads native Arrow,
play-ready JSON, standalone HTML, CSV, story-source ZIP and compiled Dialogic ZIP
exports. The Godot/Dialogic runtime and optional Python/MCP services still run on
their own hosts; the browser is a client of that same authoritative service.

## Build

Use Godot 4.6.3 and its matching **no-threads** web export template. The Web preset
is self-contained: it uses Godot's built-in HTML shell and `web/offline.html`.
It no longer depends on a sibling website checkout. Thread support is disabled;
no COOP/COEP cross-origin isolation headers are needed.

With templates installed through Godot's export-template manager:

```sh
python3 tools/export_pwa.py --godot /path/to/godot --output /tmp/Arrow-PWA
```

On Linux, the optional installer downloads pinned official Godot/template assets,
checks their committed SHA-512 digests and extracts only the needed files. The
full upstream template download is approximately 1.2 GB; the installed web
runtime and template are much smaller:

```sh
python3 tools/install_web_toolchain.py --directory /tmp/arrow-web-tools
python3 tools/export_pwa.py --godot /tmp/arrow-web-tools/godot \
  --template /tmp/arrow-web-tools/web_nothreads_release.zip --output /tmp/Arrow-PWA
```

The output directory must be new and outside this source checkout. The builder
imports/exports an isolated copy, preserving tracked source/import metadata.
Build logs are written to a sibling `.Arrow-PWA-buildlogs` directory, outside the
published site. Serve the output over HTTPS, or HTTP on localhost for development;
opening `index.html` directly as a local file does not run the PWA.

## Host beside the story service

Install/configure the service as described in [story-service.md](story-service.md).
Keep its bearer token in the service's private environment. Serve the exported
PWA from the same process:

```sh
python -m services.story_service serve --web-root /tmp/Arrow-PWA
```

Open **http://127.0.0.1:8765/app/** locally. For remote hosting put the service
behind an HTTPS reverse proxy. The PWA defaults its service URL to the current
origin; enter your token in **Arrow → Story service… → Connect**. Tokens remain
in memory and are not written into exports, browser storage or the PWA cache.
Reloading requires entering the token again. The static editor files are public;
all story API operations still require a bearer token.

For a PWA hosted on a different origin, configure an exact allowlist in the
service environment before starting it:

```sh
export ARROW_STORY_ALLOWED_ORIGINS='["https://YOUR_PWA_HOST"]'
```

Use origin values only, without a path or trailing slash. HTTPS is required
except for loopback development origins. Wildcards are rejected. CORS allows
`Authorization` and `Content-Type` for GET/POST, without cookies. It does not
replace token authentication. Set the PWA's service URL to the full HTTPS service
base URL, including a reverse-proxy prefix when applicable. Redirects are refused
before credentials can be forwarded. API responses use `Cache-Control: no-store`.

MCP and CLI clients continue to connect directly to the service. Their proposals
appear in the PWA's normal review queue; PWA proposals are visible to those
clients. No Python process or stdio MCP server runs inside the browser.

## Import, edit and export

1. Enter a review message and choose **Import story ZIP…**. Use a story-source ZIP
   containing one manifest and all its referenced chapters. A downloaded Dialogic
   ZIP also contains those sources. A complete service bundle JSON containing
   `manifest_path`, `package` and `documents` is also accepted. A lone manifest
   cannot grant a browser access to sibling files on your device; package them
   together. Desktop retains its native manifest picker and also accepts ZIPs.
2. The service validates the import before creating revision 1. Load/open chapters
   and use **Shared story settings…** exactly as in the
   [editor guide](story-ui-mcp.md). Browser file dialogs choose/download local
   files; they do not access native desktop filesystem paths.
3. **Export loaded story ZIP** downloads the manifest and original chapter JSON
   values as a portable package. It can be imported again. Filenames identify
   the loaded revision. Apply and reload local proposals first to include their
   changes in a whole-story export.
4. **Export loaded story for Dialogic** runs the same native compiler/linker in
   WebAssembly and downloads its timeline, character resources, manifest, sources,
   `project-snippet.godot` and integration instructions. Unsupported source content
   stops the export with diagnostics. Compilation does not reset game variables.
5. For the currently open local chapter, the existing project export menu still
   offers **Download a Copy**, **Export JSON**, **Export HTML** and **Export CSV**.
   A native copy retains editable Arrow data; HTML is the original single-chapter
   Arrow runtime. Cross-chapter package playback uses the compiled Dialogic
   export, as on desktop.

For a Dialogic ZIP, copy `dialogic_export/` and `sources/` into a Godot project
with Dialogic **2.0-alpha-20**, pinned at
`e301e1bb5e62e6de22c6b7748c4372d0570bb4a7`. Merge the snippet's Dialogic settings
with existing settings, preserving authored defaults and customized characters;
**do not replace your whole project.godot**. The README gives the timeline path
for `Dialogic.start`. For repeated imports with preservation/publication and
stale-dependency checks, use the native Arrow importer on the exported sources.
The browser cannot write directly into your game project or run Godot's editor
plugin there.

ZIP import reads bounded entries in memory without extracting paths into the
filesystem. It rejects traversal/duplicate paths, symlinks, encrypted/ZIP64 or
unsupported-compression entries, and oversized inventories. Limits are 32 MiB
uploaded ZIP, 64 MiB declared expanded ZIP, 512 entries, and the service's 8 MiB
complete source bundle / 128 chapters.

## Offline and browser storage

Open the PWA online to finish caching its runtime, then install it using your
browser's install action. The service worker caches the editor, not API responses.
Local projects/bindings and the last loaded story snapshot are stored in the
browser's IndexedDB-backed Arrow filesystem. After loading/saving, allow browser
storage synchronization to finish before closing the tab. Browser storage is
origin-specific and can be removed by browser cleanup; keep downloaded backups.

Offline, the installed editor can reopen saved local chapters and use their
Arrow/JSON/HTML/CSV exports. The cached loaded story can produce source and
Dialogic ZIP downloads offline, including after reload. Service imports,
revision reads, proposal creation and apply require the service to be reachable.
A failed connection leaves local work intact; old chapter/batch revisions are
never silently advanced. Unsubmitted shared-settings batches remain session-only
as on desktop, so propose them before quitting to persist them on the service.

## Validation

`tests/web/run_pwa_tests.py` drives an actual exported Godot WebAssembly editor in
Chromium through a test-only entrypoint. It exercises the real browser file picker,
service CORS/authentication, graph and shared-settings edits, review/apply/conflicts,
all download formats, browser persistence, offline reload/export and redirect
isolation. It also boots the uninstrumented production build and checks that test
hooks are absent. The normal production export never adds that entrypoint.

`tests/web/check_dialogic_export.py` loads the browser-downloaded ZIP in a separate
pinned Dialogic project and plays both branches, checking shared speakers, variables
and source dependencies. Native archive tests compare in-memory and file-based
compilation, ZIP round trips and rejected input. GitHub Actions builds both PWAs,
runs these checks and uploads the production **Arrow-PWA** artifact.

Chromium/Linux and pinned Godot/Dialogic are covered. OS-level PWA installation,
Safari/Firefox, mobile/touch layouts and the actual Jade Lantern game remain
unverified. No public hosting is deployed by these build/test tools.
