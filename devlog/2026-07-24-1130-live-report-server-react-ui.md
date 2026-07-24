# Devlog: Live Report Server and React UI

Updated: 2026-07-24 11:30  
Author: Lichen Zhu  
Baseline commit: `c5f91ef Add report command: interactive single-file HTML attention dashboard`

## Summary

`focusledger report` no longer writes a dead HTML snapshot to disk. It now
starts a loopback-only HTTP server and opens
`http://127.0.0.1:<port>/`, where a React UI polls a JSON API that re-derives
the day's timelines from raw events on every request. Leaving the tab open
while the agent runs shows the day filling in.

The static snapshot is preserved as `--export`, and runs the same frontend
code with the data inlined — one UI, two delivery modes.

## Why the snapshot had to go

The generated file froze the derivation at render time, so "watch today" meant
re-running the command and reopening a new file. Since every other FocusLedger
view already derives at query time, freezing it in the one place a human
actually looks was the wrong seam.

## Architecture

```text
agent  -> SQLite raw_activity_events
report -> ReportServer (127.0.0.1, ephemeral port)
            GET /                 index.html
            GET /static/*         app.js / app.css / vendored React
            GET /api/report?date= re-derives per request  <- live data
            GET /api/status       heartbeat + staleness
browser -> React app polls /api/report at the agent's poll interval
```

`services/report.py:build_report_data` is unchanged and is now shared by the
API and the exporter. The server holds no derived state of its own.

### Loopback only, deliberately

This is a personal attention record, so `create_server` raises on any non
loopback host rather than accepting a bind address from the caller. Combined
with a resolve-and-verify guard on static paths, the exposure surface is a
single machine-local port that serves three routes. A test asserts the bind
address and that traversal attempts (`..`, percent-encoded `..`) return 404.

## React without a build step

The requirement was React; the constraint is that FocusLedger is a
dependency-free Python CLI, and no user should need Node or `npm install` to
open their own report.

Resolution: vendor the React 18 and ReactDOM UMD builds plus `htm` (~145 KB
total) into `server/static/vendor/`. `htm` gives JSX-like tagged-template
syntax with no transpile, so the app is real React — components, hooks, virtual
DOM diffing — that runs straight from a `<script>` tag. No bundler, no
toolchain, offline after install.

The earlier decision to reject React was made for a generated single file,
where a runtime bought nothing. Once the deliverable became a live app with
polling, shared zoom state and range-scoped panels, the component model earns
its weight.

### Rendering performance

Cursor movement must not rebuild hundreds of SVG rects. Segment geometry
(skyline, lanes, density buckets, ticks, gap annotations) is computed in
`useMemo` keyed on `[data, view, width]`; only the crosshair, tooltip and
readout re-render as the pointer moves.

### Preserving zoom across refreshes

A live view that snaps back to full-day on every poll would be unusable. `view`
lives in state independent of `data`; it initialises once from the domain and
is only re-clamped when the domain grows. If the viewport was pinned to the
right edge, it follows the new edge — the "keeps up with the market" behaviour
of a live chart — and stops following as soon as the user pans back.

## Frontend bugs caught in review

Three, none of which a browser was available to find:

- **`class=` vs `className`.** htm forwards attributes verbatim, so every
  `class=` in the templates would have been handed to `React.createElement`.
  113 occurrences fixed; a test now asserts `class=` never returns.
- **Stale closure in the poll loop.** The retry delay read `data` captured at
  mount (always `null`), so it silently pinned to the 5 s fallback instead of
  the configured poll interval. Now read off the fetched payload.
- **Ref driving a className.** The pan cursor was derived from `dragRef.current`,
  which cannot trigger a re-render. Moved to state.

## Verification

No browser or Node here, so the JS is checked two ways. `esprima` (pure Python,
fetched into a scratch dir — not a project dependency) parses `app.js` and all
three vendored libraries, which also proves the downloads were not truncated.
`tests/test_frontend_static.py` then guards the failure modes that silently
produce a dead page: no `offsetX`, `className` not `class`, every referenced
component defined, inert SVG marks, both data modes wired, bracket balance,
zoom floor enforced, no external origins.

```text
63 tests OK (51 prior + 12 server)
end-to-end: server serves index/API/assets; traversal -> 404; bad date -> 400;
            external IP -> connection refused; new rows appear without restart
```

Runtime feel still needs a human at a browser; these only rule out the dead
page class of failure.

## Zoom

Floor lowered from 30 minutes to **2 minutes** — at a 5 s poll that is ~24
samples, so it resolves individual app switches. Added 15m / 1h / 3h / Day
preset buttons (centred on "now" while live), a snappier wheel factor, and a
dashed "now" marker on the time axis.

## Files

- `focusledger/server/app.py` — new: server, routes, loopback guard
- `focusledger/server/static/{index.html,app.js,app.css}` — new: React UI
- `focusledger/server/static/vendor/*` — vendored React 18 + htm
- `focusledger/services/report.py` — `render_report_html` now inlines the app
- `focusledger/cli.py` — `report` serves; `--port`, `--no-open`, `--export`
- `focusledger/report/` — removed (superseded by the React UI)
- `tests/test_server.py`, `tests/test_frontend_static.py` — new

## Open edges

- Polling, not SSE/WebSocket. At a 5 s poll rate the payload is small and the
  logic is trivial; a push channel is only worth it if the payload grows.
- The whole day's segments are sent on every poll. Fine for a day; a
  multi-day view would need incremental fetches.
- Deep-work thresholds (25 min / 5 min slack) are still frontend constants and
  should move into `AppConfig` beside the attention thresholds.
- Vendored libraries are committed. That is the price of a no-build-step React;
  they need a manual refresh for security updates.
