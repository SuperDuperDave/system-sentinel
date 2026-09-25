# Sanitized screenshots

Five images of the real dashboard, rendering fixture data through the real code, for the studio
page's hero: a monitor housing and a phone housing drawn in code, as the overhaul's brief asked.

**The data on screen is synthetic.** Nothing here was read from any real machine. Two fixtures
feed the app's own bridge seam, so what's on screen is the same code that renders a real reading,
over records built for this purpose:

- [`fixtures/system-log.json`](fixtures/system-log.json) — ~60 synthetic System-log records
  spanning two evenings, built only from Windows' public providers, event ids and Microsoft's own
  message templates (Kernel-Power 41, EventLog 6008, Kernel-General 12, WHEA-Logger 17, Display
  4101, storahci 129, Service Control Manager 7034/7036, DistributedCOM 10016,
  Microsoft-Windows-Kernel-Boot 20, FilterManager 6).
- [`tests/fixtures/whea-records.json`](../../tests/fixtures/whea-records.json) — the WHEA-Logger
  fixture already in the test suite (a baseline, a tail, a burst and one fatal record). The
  screenshot server gives every record with binary data a real, decodable CPER payload built the way
  `tests/test_whea.py`'s `minimal_cper()` builds one. An exact System row opened from that fixture
  receives genuine decoder output, not invented JSON; the list itself is a bounded preview.

**The caption the studio may use:**

> The screens show the tool rendering a synthetic record built from Windows' own providers, ids
> and message templates, so nothing from any real machine appears.

## The images

| File | Shows | Pixels | Scale |
| --- | --- | --- | --- |
| `desktop-hardware-errors.png` | Hardware errors, 24-hour window: the burst status, the trace with the burst visible, the signatures, and the head of the records list | 2880×1800 | 2x (1440×900 viewport) |
| `desktop-record.png` | Record, "Every level" · last 200: the start triad in the list, a Kernel-Power 41 row open with "The record before this" expanded | 2880×1800 | 2x (1440×900 viewport) |
| `phone-record.png` | Record on the phone: a Kernel-Power 41 row open, the record before it expanded, scrolled so the opened row leads the viewport | 1170×2532 | 3x (390×844 viewport) |
| `phone-hardware-errors.png` | Hardware errors on the phone, from the top | 1170×2532 | 3x (390×844 viewport) |
| `phone-sign-in.png` | The sign-in screen, empty field | 1170×2532 | 3x (390×844 viewport) |

Each was asserted to have `document.documentElement.scrollWidth` equal to its viewport width
(1440 or 390) — no horizontal overflow on any of the five.

`desktop-hardware-errors.png` is scrolled down from the top: the Status section is the earliest of
the three things asked for, so per the brief it's the binding constraint — the capture script
scrolls only as far as leaves the Status heading just inside the top edge, which keeps Status and
the trace intact but leaves no room to also show a full record row (the "Records" header and its
summary line are the last things visible). Status and trace took priority, as instructed.

## Captured from

- Commit `dbe815e` (`git rev-parse --short HEAD`). The working tree at capture time also carried
  an uncommitted, unrelated in-flight change (a concurrent agent's one-time-code sign-in route for
  the double-click launcher, in `sentinel/app.py`, `sentinel/auth.py`, `sentinel/cli.py`,
  `sentinel/launcher.py`) — additive only, touching no reading, redaction, or view code these
  screens depend on.
- Browser: Playwright's bundled Chromium 153.0.8010.12 (Playwright 1.63.0).
- Device pixel ratios: 2 (desktop), 3 (phone, iPhone-class density).

## How to reproduce

1. **Build the dashboard**, if `sentinel/static/index.html` is missing:
   ```
   cd dashboard && npm run build
   ```
2. **Start the fixture server** from the repository root. It wraps `sentinel.app.create_app` with
   a bridge that answers WHEA and System-log scripts from the two fixtures above and everything
   else empty — nothing in `sentinel/`, `dashboard/src/` or `tests/` is touched to do this:
   ```
   SYSTEM_SENTINEL_HOME=/some/scratch/dir ./.venv/bin/python docs/screens/fixtures/fixture-server.py --port 8021
   ```
   The token it mints is at `$SYSTEM_SENTINEL_HOME/token`. Use a scratch directory, never the
   real data directory (`~/.system-sentinel/` or `%LOCALAPPDATA%\SystemSentinel\`).
3. **Capture.** `capture.cjs` needs `playwright` on its module path; it isn't a repository
   dependency, so install it in a scratch directory (as this session did) and point `NODE_PATH`
   at it:
   ```
   npm install playwright --prefix /some/scratch/dir
   NODE_PATH=/some/scratch/dir/node_modules \
     PORT=8021 OUT=docs/screens TOKEN=$(cat /some/scratch/dir/token) \
     node docs/screens/fixtures/capture.cjs
   ```
   It signs in, drives both views (opening a Kernel-Power 41 row and expanding "The record before
   this" where the spec calls for it), scrolls each shot into place, asserts no horizontal
   overflow, and prints each `scrollWidth` it measured.
4. Stop the server by its port (`lsof -ti tcp:8021 | xargs kill`), not by process name.

`fixture-server.py` finds the repository root by walking up from its own path (three levels, as
committed); `SENTINEL_REPO_ROOT` overrides that if the script is copied somewhere else.

The in-place cited-record interaction has a separate synthetic browser check. With the fixture
server and Playwright module path above running, use:

```
NODE_PATH=/some/scratch/dir/node_modules TOKEN_FILE=/some/scratch/dir/token PORT=8021 \
  node docs/screens/fixtures/check-cited-records.cjs
```

It intercepts only synthetic Crash, Signals and exact-record reading envelopes. At 1440 and 390
pixels it checks that opening a stop or lead leaves its control in place, an exact lookup happens
only after a click, and same/reused/missing/failed/denied/transport-loss results remain distinct.
The fixture also checks System WER and Application report roles, missing or ambiguous held rows,
grouped and unusable references, and that a changed citation on retake starts closed. It checks
horizontal overflow in both views. Set `SCREENSHOT_DIR` to an existing scratch directory to save
the inspected screens; they are not part of the five published screenshots.

The investigation-continuity check uses the same fixture server, `TOKEN_FILE`, `PORT` and
`NODE_PATH` setup:

```
NODE_PATH=/some/scratch/dir/node_modules TOKEN_FILE=/some/scratch/dir/token PORT=8021 \
  node docs/screens/fixtures/check-investigation-continuity.cjs
```

At desktop and phone widths it checks browser Back and the direct return from a Record moment,
the exact Crash and Signals source-link positions and focus, a stop and fault open together, a selected dump header,
a stop's on-demand Changes reading with its exact estimated boundary, per-source denial, returned raw row and held return,
separate windows for two stop times, and no before-stop request when no estimate exists,
a near-top stop on desktop, an open Signals lead after navigation, no new observed questions on
return, visible held evidence during a slower failed retake, transport loss, and a new question
after a 401 and fresh sign-in. It also checks that an unanswered changed count may retry on return
without moving focus later. Its readings are synthetic;
it does not measure the installed application's timing or prove the reported click path.

The saved-capture Contents check uses the same fixture server and Playwright setup:

```
NODE_PATH=/some/scratch/dir/node_modules TOKEN_FILE=/some/scratch/dir/token PORT=8021 \
  node docs/screens/fixtures/check-capture-contents.cjs
```

At desktop and phone widths it verifies that the manifest and selected saved reading load only
after their controls open, neither action takes a new machine reading, original capture time and
redaction state are visible, complete saved JSON is reachable, and a refused member leaves the ZIP
download available. The capture answers are synthetic, so this checks interface behavior and
overflow rather than a particular person's saved archive.

## The social preview

`social-preview.png` is the card a shared link to the repository shows: 1280×640, the identity's
field with the graticule in one zone, the mark drawn by the tool's own
`sentinel.launcher.render_mark`, the wordmark, one line, and a crop of
`desktop-hardware-errors.png` in a thin frame — which is why it inherits that image's privacy
check above, and why it carries no date. It is made by
[`make_social_preview.py`](make_social_preview.py), so a new capture or a changed mark is one
command away:

```
./.venv/bin/python docs/screens/make_social_preview.py
```

It needs Pillow (`pip install pillow`, or the `[launcher]` extra, which the mark needs anyway).
The display face is Azeret Mono when a TTF or OTF of it is on disk; only the WOFF2 the dashboard
serves is in this repository and Pillow cannot read that, so the card as committed is set in
DejaVu Sans Mono. GitHub takes it under *Settings → General → Social preview*.

## Privacy check

Every image was inspected directly (not just its source data) for a serial number, a host name
other than the fixture's own placeholder, a user name, a local path under a user profile, or
message text this machine actually logged. The dashboard's own redaction also ran for real: the
fixture's identity probe answers host `WORKSTATION` / user `person`, and the app's `Redactor`
replaced every occurrence of `WORKSTATION` with `<host>` before it reached the browser — so even
the placeholder host name doesn't appear on screen, only the redaction placeholder. The two
fixture files were grep'd for this machine's real computer name and Windows user name; neither
appears.

## Dates on screen

The Record captures carry the synthetic record's own dates: the day headers and, in the opened
record, the exact `Time` field the tool shows so a moment can be copied into the `record` reading.
Those dates are two evenings before the capture and will age. The Hardware errors captures show
only clock time and "now". Under the page's rule (relative time or none), `desktop-hardware-errors`
and `phone-hardware-errors` are date-free; the Record captures show the composer's mechanism and
carry a date the studio may crop or accept. A re-capture through `fixtures/capture.cjs` always
yields records dated relative to the day it runs.
