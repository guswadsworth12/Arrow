# Cloudflare Access sign-in

Put the hosted PWA and story service behind [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/applications/)
so people sign in once (email code, Google, GitHub, …) instead of pasting the shared
bearer token after every reload. The service verifies the identity Access signs for
each request and records it on every import, proposal and apply.

This is optional. Without the settings below the service behaves exactly as before:
every `/v1/` request needs the bearer token.

## How it fits together

```
browser ──HTTPS──> Cloudflare Access ──tunnel──> reverse proxy ──> story service (+ PWA files)
          sign-in cookie          adds Cf-Access-Jwt-Assertion        verifies the assertion
```

- The PWA and `/v1/` API share one origin. The PWA sends the Access sign-in cookie
  only to that origin; it never sends cookies to another service URL.
- Access adds a signed `Cf-Access-Jwt-Assertion` header to every request it lets
  through. The service checks its RS256 signature against your team's published keys
  (`https://TEAM.cloudflareaccess.com/cdn-cgi/access/certs`), its audience, issuer and
  expiry. A forged or replayed header from another network path is rejected.
- When that header is present it alone decides who is acting. Requests without it
  (CLI or MCP on the host, SSH tunnels) still use the bearer token, recorded as
  `bearer-token`.

## Cloudflare dashboard

In **Zero Trust → Access → Applications**:

1. **Add an application → Self-hosted**, for the Arrow hostname (no path).
   Choose a session duration, e.g. 1 month. Add an **Allow** policy listing the people
   who may use Arrow (emails, or an email domain). Enable an identity provider under
   **Settings → Authentication** (the built-in **One-time PIN** works without setup).
2. Add a second **Self-hosted** application for the same hostname with paths
   `index.manifest.json` and `index.*.png`, and a single **Bypass / Everyone** policy.
   Browsers fetch the install manifest and its icons without cookies; these files hold
   only the app name and icons. Everything else, including the editor and API, stays
   behind sign-in.
3. Open the first application's **Overview** and copy its **Application Audience (AUD) Tag**.
   Your team domain (`https://TEAM.cloudflareaccess.com`) is shown in the Zero Trust
   **Settings** as the team name/domain.
4. Optional, for the CLI, MCP adapter or desktop Arrow from outside your network:
   **Access → Service Auth → Create service token**, then add a **Service Auth** policy
   for that token to the first application.

## Service configuration

Add both values to the service's private environment and restart it:

```sh
ARROW_STORY_ACCESS_TEAM_DOMAIN=https://TEAM.cloudflareaccess.com
ARROW_STORY_ACCESS_AUD=<Application Audience (AUD) Tag>
```

Startup fails if only one is set. The bearer token (`ARROW_STORY_API_TOKEN`) is still
required. The reverse proxy must pass the `Cf-Access-Jwt-Assertion` request header
through unchanged (Caddy and nginx do by default).

## Using it

- **PWA**: open the site, sign in, then **Arrow → Story service…**. With the token field
  blank the window connects automatically and shows *Connected as you@example.com*.
  The installed PWA opens from its offline cache, so an expired sign-in shows up as a
  redirected request; choose **Sign in…** to renew it (Arrow reloads; save local work
  first). A token can still be entered instead.
- **CLI, MCP adapter and desktop Arrow**: keep `ARROW_STORY_API_TOKEN`, and to pass
  through Access also set `ARROW_STORY_ACCESS_CLIENT_ID` and
  `ARROW_STORY_ACCESS_CLIENT_SECRET` from the service token. Their changes are recorded
  as `service-token:<client id>`.
- `GET /v1/session` returns `{"actor": ..., "via": "cloudflare-access" | "bearer-token"}`.

## What is recorded

| Record | Field | Value |
| --- | --- | --- |
| Revision (import or apply) | `committed_by` | Who imported or applied it |
| Proposal | `created_by` | Who proposed it |
| Proposal | `applied_by` | Who applied it (a repeated apply keeps the first) |

Values are the signed-in email, `service-token:<client id>`, or `bearer-token`.
Databases from earlier versions are migrated in place on first start (schema version
2); their existing rows show no identity. The review window lists who proposed and
applied each change.

## Limits

- Everyone the Access policy allows can import, propose and apply. There are no roles.
- The bearer token still grants full access without an identity; keep it private.
- Access sign-in itself is Cloudflare's: MFA, session length and allowed people are
  configured there, not in Arrow.
- Tests verify assertions signed with local keys and a simulated Access edge; a real
  Cloudflare account is not part of CI.
