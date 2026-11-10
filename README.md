# Movies

A private watch queue for films and series: add titles, reorder and bump them, open a detail card,
swipe a row to mark it watched or archive it, keep a dated watch history and queue rewatches.
Installable as a PWA. Packaged as a Wizard OS managed app (`wizard.md`, Runtipi recipe, `/api`,
`/mcp`, `/healthz`, view at `/`).

## Managed packaging

`runtime.package: source` runs the stdlib app on the official Python multi-architecture
image pinned by index digest, with `${APP_SOURCE_DIR}:/app:ro`. No image build or GHCR
publication is needed. The existing entrypoint still prepares root-owned app-data and drops
to UID/GID 1000; the read-only filesystem and capability restrictions are unchanged.
The compose carries the Dockerfile's environment, working directory, command and healthcheck.
The App Manager must supply `APP_SOURCE_DIR` (wizard-app-manager#153); do not merge this
recipe until that support ships in a Wizard release. The Dockerfile remains a development option.

## Run locally

```bash
CINEQUEUE_DATA=./data CINEQUEUE_BASE_PATH= python3 -m cinequeue   # http://127.0.0.1:8765/
```

## Configuration (all optional)

| Variable | Default | Meaning |
|---|---|---|
| `CINEQUEUE_DATA` | `./data` (`/data` in the image) | The only writable directory: `movies.json`, `add_requests.json`, where-to-watch cache. |
| `CINEQUEUE_WATCH_REGION` | empty in the image | Two-letter country for JustWatch availability. Empty hides the panel and makes no lookups. |
| `CINEQUEUE_TIMEZONE` | `UTC` | IANA zone for "watched today" dates. |
| `CINEQUEUE_ADMIT` | `disabled` | `live` hands "find and add" requests to the Wizard Agent Service; `stub`/`fail` are QA fakes. |
| `CINEQUEUE_WIZARD_ORIGIN` | empty | Agent Service origin, required for `live`. |
| `CINEQUEUE_AGENT_NAME` | `your Wizard` | Name shown on the request sheet when live. |
| `WIZARD_TRUSTED_HOSTS` | empty | Exact hosts accepted from the Wizard gateway; filled by Wizard during install. |
| `WIZARD_TRUSTED_ORIGINS` | empty | Exact browser origins accepted from the Wizard gateway; filled by Wizard during install. |
| `CINEQUEUE_ORIGINS` | empty | Legacy extra exact browser origins; kept for existing installs. |
| `CINEQUEUE_BASE_PATH` | `/cinequeue` (empty in the image) | Path prefix when not behind a prefix-stripping proxy. |

No third-party API keys are needed. Without agent admission the add sheet adds simple cards
directly; enriched metadata (poster, IMDb rating, cast…) is filled in by an agent through
`cinequeue_update` when the integration is enabled.

## Oscar / second Wizard setup

The app id is `movies`, the app's name (it was `cinequeue` until 0.3.0; wizard.md `renamed_from: [cinequeue]`
lets the App Manager move an existing install, data and connections included, to the new id). The MCP tool prefix
and `CINEQUEUE_*` settings keep their names so agents and saved settings keep working. All display labels are Movies. Open the managed view through Wizard, where the central
`/wizard-os-home/core/app-client.js` resource is served. Do not copy platform code into the app.
For direct-port standalone use set `CINEQUEUE_SHARED_ORIGIN` to the reachable Wizard origin;
the app proxies only that one centrally maintained script. This defaults to the agent origin.

To enable enriched Add, set `CINEQUEUE_ADMIT=live` and `CINEQUEUE_WIZARD_ORIGIN` to the
reachable Wizard Agent Service origin. Verify `/api/config` reports `agent.ready: true`.
Register the app MCP endpoint with the owner's agent, or set `CINEQUEUE_AGENT_API_URL`
to this instance's agent-reachable HTTP origin so the run can use its OpenAPI/MCP directly.
Do not use another owner's URL. Required agent endpoints are `/wizard/hermes/api/sessions`
and `/wizard/hermes/v1/runs`; the configured origin must expose those paths. Use the
installation's network/service mapping, not a hard-coded personal hostname.

Test a real named film with year through Add; observe dispatched → processing → completed,
then read the card back. A completed request is only valid after metadata verification.
Disabled admission explicitly offers simple cards instead, never claims enrichment.
New installs seed Batman Begins (2005) once; an existing queue, including intentional `[]`,
is never reset. No owner queue, history or requests are included in the images.

## Feature switches

New features ship off by default behind owner switches (`wizard.app-switches/1`, wizard-os `docs/APP_SWITCHES.md`).
State lives on the box in `CINEQUEUE_DATA/switches.json`; `GET /api/switches` lists them and `POST /api/switches`
(`X-Wizard-Switch: 1`, JSON `{"id", "on"}`) flips one, so the Tracker's Try it toggle works through the App Manager.

| Switch | Issue | What it changes |
|---|---|---|
| `friendly-phone` | movies#14 | Unwatched / Watched / Add in a bottom thumb bar on phones (beside the tabs on wide windows), always-visible row menu, readable Mark watched, 44 px touch targets, helpful empty states. |

## Test

```bash
python3 -m pytest -q tests            # synthetic only; browser tests need Playwright Chromium
NODE_PATH=<playwright> node qa/browser_managed.cjs http://127.0.0.1:8765/   # running container, fresh data
```

## Data and migration

State is plain JSON written atomically (temp file + rename) under an exclusive `flock` on
`<store>.lock`. Back up the data directory; the image never contains owner data.
