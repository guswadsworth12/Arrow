<img src="./icon.svg" style="width: 64px; height: auto; display: block; margin-bottom: 1rem;" alt="">

Arrow
=====

***The Game Narrative Design Tool***

[Download](#download) | [Integration](#runtime--integration) | [Web App](#web-app) | [Guides](#guides)

Arrow is the free, open-source and feature-rich tool for
*game narrative* design, *text-adventure* development,
and creation of *interactive nonlinear storytelling* documents.

![Arrow's Overall Look][arrow-screenshot]

Notable Features:

+ Free as in Freedom
+ 100% Visual Development
+ Advanced Node System
+ VCS-Friendly Save Files
+ Support for Distributed Workflows
+ One-Click Playable Export (HTML)
+ CSV (I18n) Export
+ Unicode (with RTL, CJK and CTL) Support
+ Continuum Safety

Arrow supports a rich palette of features, from scenes and macros, to variables and characters.
It also comes with built-in common node types, providing logic, interactive navigation, random data generation,
state management, and more.

> Check out [Guides](#guides) for detailed information.


## Download

Arrow prebuilt executables are available to download from the archive of [releases].

Following links are to the *fairly stable most recent* versions:

+ Linux (X11) [x86_64 (64-bit)][linux-x11-x86-64-latest]
+ Windows [64-bit][win-64-latest]

> For other platforms and architectures, or to take advantage of the latest features in the main branch
> consider [building or running Arrow from source][wiki-build-from-source].


## Web App

This fork's installable [PWA](docs/pwa.md) includes the shared story service UI,
chapter graphs, shared definitions/links, proposal review/apply, and all exports.
Download the **Arrow-PWA** artifact from this fork's
[build workflow](https://github.com/guswadsworth12/Arrow/actions/workflows/dialogic-bridge.yml),
or build it with `tools/export_pwa.py`. The guide covers self-hosting and service
connections.

Projects/configuration are stored in the browser. Native Arrow, play-ready JSON,
standalone HTML, CSV, story-source ZIP and compiled Dialogic ZIP downloads provide
portable copies. Cached local chapters and loaded stories can export offline;
shared revisions/proposals use the same backend as desktop and MCP clients.

A modern browser needs WebGL 2, Canvas, JavaScript and browser storage enabled.
The UI is optimized for medium-large screens. Browser file selection/downloads
replace native filesystem dialogs, and operating-system window controls remain
platform-specific. Upstream Arrow's [original PWA demo][web-app] is also available.


## Runtime & Integration

+ [Bow]
    > With a complete Arrow runtime in its core, and much more, Bow is our narrative software development kit developed as a minimal/demo open game.

+ [Official HTML-JS Runtime][runtime-html-js]
    > Bundled with Arrow, this runtime is used in playable exports.


## Guides

Docs for Arrow are available in the [repository's Wiki][wiki-home],
including a [Quick Start Guide][wiki-quick-start-guide] as well as,
detailed instructions, documentation of the built-in nodes, and more.


## Licenses

Copyright (c) 2021-present Mor. H. Golkar and contributors

Unless otherwise specified, Arrow and files in this repository are
available under `MIT` license.
See [license][license-file] & [copyright][copyright-file] files for more information.


Have a Good Time



<!-- Download -->
[releases]: https://github.com/mhgolkar/Arrow/releases
[linux-x11-x86-64-latest]: https://github.com/mhgolkar/Arrow/releases/download/v3.1.0/Arrow-v3.1.0-linux-x86_64.tar.gz
[win-64-latest]: https://github.com/mhgolkar/Arrow/releases/download/v3.1.0/Arrow-v3.1.0-win.64.zip
<!-- PWA -->
[web-app]: https://mhgolkar.github.io/Arrow/
<!-- Wiki -->
[wiki-home]: https://github.com/mhgolkar/Arrow/wiki/
[wiki-build-from-source]: https://github.com/mhgolkar/Arrow/wiki/build-from-source
[wiki-quick-start-guide]: https://github.com/mhgolkar/Arrow/wiki/quick-start-guide
<!-- Relative -->
[runtime-html-js]: ./runtimes/html-js/
[license-file]: ./license
[copyright-file]: ./copyright
<!-- Resources -->
[arrow-screenshot]: https://mhgolkar.github.io/Arrow/media/overview.v3.png
<!-- External -->
[Bow]: https://github.com/mhgolkar/Bow

## This fork: Godot / Dialogic integration

An initial Arrow-to-Dialogic importer is available in `addons/arrow_dialogic`.
See [installation, supported nodes, limitations, and tests](docs/dialogic-bridge.md).
The importer targets Dialogic 2.0 Alpha 20. The optional revision service and
AI editing clients below extend that foundation; public hosting remains separate.

### Linked chapter packages

This fork can also import a [story package](./docs/story-packages.md) containing
multiple Arrow files, shared character/variable definitions, explicit chapter
links, and dependency checks. The [non-canon example](./examples/dialogic_bridge/story/guild_story.json)
uses different local IDs for the same resources in each chapter. Package manifests
retain JSON for inventory and mappings; shared names/defaults and Jump routing
can be edited through the desktop service UI below.

### Shared story service

An optional [HTTP story service and CLI](docs/story-service.md) now provide SQLite
revision history, compiler-validated edit proposals, conflict detection and
reversible commits. It uses the same package compiler as the Godot addon. Arrow
can exchange source checkouts through the CLI or use the desktop UI below.
Hosted behind [Cloudflare Access](docs/cloudflare-access.md), people sign in instead
of sharing a token, and every import, proposal and apply records who made it.
An [OAuth-protected remote MCP deployment](docs/remote-mcp.md) connects compatible
hosts to the same workspace, with optional Google sign-in through Keycloak.

### Arrow UI and MCP

The desktop and PWA **App menu → Story service…** opens service chapters in Arrow,
proposes graph edits, and reviews/applies shared proposals. **Shared story
settings…** stages character names, typed defaults and chapter Jump destinations
without hand-editing the manifest. The optional
[official-SDK MCP adapter](docs/story-ui-mcp.md) reads and proposes edits over the
same HTTP backend; explicit MCP commits can be enabled when configuring a host.
Revision conflicts preserve local work. See the guide for launch settings and
the tested Arrow/MCP round trip. Remote hosting uses Streamable HTTP, signed
tokens and read/edit/commit roles; public deployment requires your server/domain.

### PWA build and exports

The [self-contained PWA](docs/pwa.md) includes shared story editing/review and
browser package import, with Arrow/JSON/HTML/CSV, source ZIP and Dialogic ZIP
downloads. Cached chapters/stories can export offline. Build with
`tools/export_pwa.py`; the service can host the result at `/app/`. GitHub Actions
validates real browser workflows and uploads a deployable Arrow-PWA artifact.
