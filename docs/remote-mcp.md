# Remote Arrow MCP with Google sign-in

Arrow supports authenticated **Streamable HTTP** at `https://WORKSPACE_DOMAIN/mcp`,
alongside the existing local stdio adapter. Both use the same story service,
SQLite revisions, native compiler, proposal review and optimistic conflicts.
The PWA sees remote proposals in its normal queue and retains all its exports.

The supplied self-hosting stack uses Caddy for HTTPS, Keycloak for OAuth/PKCE and
optional Google sign-in, Postgres for accounts, and the Arrow service/MCP sidecar.
A Gmail account alone does not supply a server, domain or Google OAuth client.
This package prepares deployment; it does not create a public endpoint or enable
Google automatically. Hosting costs depend on your chosen provider.

## Before deployment

You need a Linux **x86_64** server with Docker Engine/Compose, persistent storage,
and two DNS names pointing to it, such as `workspace.example.com` and
`auth.example.com`. Allow public TCP ports 80/443 for Caddy's HTTPS certificates.
Only Caddy publishes ports; do not publish the story, MCP, database or Keycloak
container ports separately. Keep the deployment directory private to operators.

In the MCP host's app/connector configuration, select OAuth and copy its **exact
redirect URI**. For ChatGPT, use the URI displayed in its management UI; callback
formats depend on the host configuration. Do not guess or use a wildcard. The
dedicated public client ID is `arrow-chatgpt`, authorization-code with PKCE S256,
token endpoint authentication `none` (no client secret). Hosts must support this
preconfigured public client; this package does not enable unrestricted dynamic
client registration. See [OpenAI's authentication guide][openai-auth].

Install the pinned Python MCP dependencies as in [story-ui-mcp.md](story-ui-mcp.md),
then run from the checkout root, replacing the example values:

```sh
python deploy/remote-mcp/prepare.py \
  --workspace-domain workspace.example.com \
  --auth-domain auth.example.com \
  --redirect-uri 'EXACT_HTTPS_CALLBACK_FROM_YOUR_MCP_HOST'
docker compose -f deploy/remote-mcp/compose.yml config --quiet
docker compose -f deploy/remote-mcp/compose.yml up --build -d
```

The preparer generates a mode-0600 `.env` with independent random service,
database and bootstrap-admin credentials, and a public `generated/arrow-realm.json`.
It refuses to overwrite an existing deployment. Both paths are gitignored; never
publish `.env`, Docker's resolved environment, tokens or database backups. The
image build excludes deployment secrets and verifies the official Godot 4.6.3
download with a pinned SHA-512 digest. Builds behind a trusted TLS inspection
proxy can supply their CA bundle as a BuildKit secret named `build_ca`; TLS and
package signatures remain verified.

Keycloak imports the realm only on first creation. Later edits to generated JSON
do not update a live realm: change its clients/providers/roles in the admin UI.
Create a permanent administrator in Keycloak and retire its bootstrap admin after
initial setup, following [Keycloak's bootstrap guidance][keycloak-bootstrap].

## Connect Google and authorize your account

1. Open `https://auth.example.com/admin/` and select the **arrow** realm. Use the
   bootstrap credentials from the private `.env` locally; do not paste them into
   chat or a source file.
2. In Google Cloud Console, create/select a project, configure the Google Auth
   Platform consent screen, and create an OAuth client of type **Web application**.
   For a personal app in testing, add your Gmail address as a test user. Google
   may impose testing-mode token/session limits. Use this exact authorized redirect:
   `https://auth.example.com/realms/arrow/broker/google/endpoint`.
3. In Keycloak **Identity providers → google**, enter that Google client ID and
   secret, then enable the provider. The Google secret belongs only in Keycloak;
   it is different from the public `arrow-chatgpt` client used by the MCP host.
   See [Keycloak's Google provider guide][google-broker].
4. Sign in once using Google through the Arrow realm's Account Console at
   `https://auth.example.com/realms/arrow/account/`. A newly brokered account has
   **no Arrow access**. In the admin console, find your user and assign one of
   the roles below. Signing in again obtains fresh permissions.
5. Configure the MCP host with URL `https://workspace.example.com/mcp`, OAuth,
   and public client ID `arrow-chatgpt`. Request `arrow:read arrow:edit` for
   authoring; request `arrow:apply` only when enabling commits. Complete sign-in
   and consent. If your host does not support public predefined OAuth clients,
   it needs a separately configured compatible client; do not supply Google's
   client secret or the private Arrow service token as a workaround.

| Keycloak role | Permitted MCP actions, when the token also has the scope |
| --- | --- |
| `arrow-reader` | Read stories, revisions, proposals and the contract (`arrow:read`). |
| `arrow-editor` | Read and propose edits (`arrow:read`, `arrow:edit`). |
| `arrow-committer` | Read, propose, and commit reviewed proposals (`arrow:read`, `arrow:edit`, `arrow:apply`). |

Commit tools are disabled by default even for committers. To expose them, set
`ARROW_MCP_ALLOW_APPLY=true` in `.env` and recreate the MCP container with
`docker compose -f deploy/remote-mcp/compose.yml up -d mcp`. Review each proposal's
diff and diagnostics before authorizing a commit in the host. Concurrent stale
proposals still fail with `revision_conflict`; invalid candidates cannot commit.
Restoration creates a new revision and preserves history.

These roles cover the entire workspace, not individual stories. Tokens use a
five-minute lifetime; role removal takes effect when existing tokens expire.
The backend records proposal messages/revisions under its existing contract,
not a verified per-user audit identity. Use a separate stack for an independently
authorized workspace. Google tokens are never accepted directly by Arrow or
forwarded to the story service; Keycloak issues Arrow-audience access tokens and
the MCP sidecar uses a separate private backend credential.

## PWA and existing clients

Build/download a production PWA using [pwa.md](pwa.md), copy its files into
`deploy/remote-mcp/site/`, set `ARROW_STORY_WEB_ROOT=/site` in `.env`, and recreate
the story/MCP containers. Make only the public site readable by the non-root
container with `chmod -R a+rX deploy/remote-mcp/site`; keep `.env` private.
Open `https://workspace.example.com/app/`. All existing
editing, source ZIP and Dialogic ZIP exports are included. The PWA and direct CLI
still connect using the private story-service token through their existing
connection UI/environment; Google login in this phase applies to **remote MCP**.
Keep that token restricted to trusted workspace operators. Static editor files
are public; API operations require authentication. Keep local stdio configurations
unchanged or use the HTTP transport with a compatible OAuth host.

## Verify and maintain

Unauthenticated `POST /mcp` must return 401 and a `WWW-Authenticate` challenge.
`https://workspace.example.com/.well-known/oauth-protected-resource/mcp` is public
and must name the exact MCP URL, Keycloak issuer and supported scopes. The issuer's
`/.well-known/openid-configuration` advertises PKCE S256 and authorization-response
issuer identification. Tokens must be RS256, signed by the configured JWKS, with
matching issuer and MCP audience, a subject, issue/expiry times, scopes and roles.
Unknown/expired/wrong-audience tokens fail closed. Redirected JWKS requests are
refused; keys refresh after at most five minutes (new-key refresh is throttled).

The service database lives in `story-data`, accounts in `auth-db`, and TLS state
in `caddy-data`/`caddy-config`. Back up those volumes plus the private deployment
configuration. For a simple consistent backup, stop the stack and copy the
volumes using Docker's documented volume-backup procedure, then restart it.
Do not use `down -v` unless deliberately deleting all stored state. Keep Keycloak,
Postgres, Caddy and the pinned Python dependencies updated and tested together.

For a different OIDC provider, run the existing story backend and configure the
MCP process with the following environment, then start it behind an HTTPS proxy:

```sh
# Export the private backend token using your process secret store as before.
export ARROW_STORY_URL=http://127.0.0.1:8765
export ARROW_MCP_PUBLIC_URL=https://workspace.example.com/mcp
export ARROW_MCP_ISSUER_URL=https://your-issuer.example.com/realm
export ARROW_MCP_JWKS_URL=https://your-issuer.example.com/realm/certs
python -m services.story_mcp --transport streamable-http --host 127.0.0.1
```

The provider must issue RS256 access tokens with that exact audience and signed
Arrow scopes. With `ARROW_MCP_KEYCLOAK_ROLES=false` (the generic default), the
provider's scope grants are the authorization policy: restrict client/user grants
there. The supplied Compose stack explicitly enables Keycloak role enforcement.
`ARROW_MCP_ALLOWED_ORIGINS` accepts a JSON array of exact browser origins; no
origin is allowed by default. Host checks use the configured public host and
proxy headers are not trusted by the MCP application. Backend calls run in worker
threads so native compilation does not block the HTTP event loop.

## Validation and limits

```sh
ARROW_STORY_GODOT=/path/to/godot python -m unittest discover -s tests/remote_mcp -v
ARROW_TEST_KEYCLOAK=1 python -m unittest discover -s tests/remote_mcp -p test_keycloak.py -v
```

The second command needs Docker, the browser-test requirements and Playwright
Chromium (`python -m playwright install chromium`), or `ARROW_TEST_CHROMIUM` pointing
to an installed Chromium. It exercises a real Keycloak container and browser with
the official MCP OAuth client: discovery, PKCE, consent, issuer/audience binding,
refresh and denied access for an account without roles. The HTTP suite exercises
the real story backend, SQLite and native compiler for permissions, proposals,
commits, invalid candidates, history and concurrent conflicts. Local tests use
loopback HTTP and disposable accounts; production imports require external HTTPS.
`docker build -f deploy/remote-mcp/Dockerfile -t arrow-remote-mcp:test .` followed
by `PYTHONPATH=. python tests/remote_mcp/check_container.py` also verifies the
non-root image, native validation, persistent revisions and private sidecar
network layout. CI runs these checks alongside existing stdio/UI/PWA tests.

Actual Google consent and a ChatGPT installation need your live host/domain and
OAuth client and have not been validated by the local tests. No public endpoint
is created by running these tests or by merging this change.

[openai-auth]: https://developers.openai.com/apps-sdk/build/auth/
[google-broker]: https://www.keycloak.org/docs/latest/server_admin/#_google
[keycloak-bootstrap]: https://www.keycloak.org/server/bootstrap-admin-recovery
