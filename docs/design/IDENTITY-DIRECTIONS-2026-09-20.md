# System Sentinel — identity directions, 2026-09-20

**Status: direction A, Instrument, selected by David on 2026-09-20 as the current implementation. On 2026-09-22 he reopened the visual identity: the page can be overhauled, with the green instrument character welcome but not binding.** Values below describe what ships today, not a constraint on a better design. Board: https://claude.ai/artifact/4RZbu4qwZ12Kr2d3ARxKvg (private). Source of the board: [identity-board/build-board.py](identity-board/build-board.py); everything on it is drawn in code.

This note owns System Sentinel's visual identity, including its presentation on mainthread.ai in `mainthread-studio`. The original direction board changed no application source; the dated refinement below is now being applied in the tool.

## Field Instrument refinement, 2026-09-22

The application is moving toward a more legible instrument while retaining the recognizable dark green field, continuous S mark, graticule and restrained phosphor light. This is a refinement of the selected Instrument identity, not a second source of token values. The studio source is read-only in the current System Sentinel task; application-only refinements remain explicit until a later studio pass can bring the two surfaces together.

The reading order is **question or state → bounded visual summary → exact evidence → method and raw readout**. A new user should understand what was observed in an ordinary sentence before reading a chart; an experienced user can inspect the exact row without losing that context. Every chart names its returned sample or stored window. Blank space means only that the sampled data has no point there; colors and shapes never imply cause or completeness beyond the source.

| App presentation value | Value | Use |
| --- | --- | --- |
| Body text | DM Sans, 16 px / 1.5 | explanation, actions, evidence summaries |
| Readout | JetBrains Mono, 13 px | times, counts, providers, exact values |
| Group label | JetBrains Mono, 12 px | labels above groups, with restraint on all caps |
| Keyboard focus | 2 px phosphor outline, 2 px offset | visible navigation light, not body text |
| Muted | `#A7C0B4` | secondary labels with clearer contrast on the dark field |
| Raised surface | `#142720` | summary panel separated from the field |
| Inspection well | `#07130F` | exact data inset within a panel |

These sizes and surfaces first apply inside the tool; the existing studio page has not been edited or visually verified for this refinement. The shared field, ink, mark and light remain as recorded below. Keep phosphor on selected, live and focus states rather than using it as paragraph text. Any warning color must carry a source-backed word or icon as well as hue.

## Selected identity (owns the values)

Direction A, Instrument, now being refined as Field Instrument in the tool. The page on mainthread.ai was the first implementation (`mainthread-studio/site`: `lib/system-sentinel-mark.ts`, `lib/system-sentinel-record.ts`, `components/SystemSentinel*.tsx`, the `instrument` surface in `styles/globals.css`). Change values here first. The studio implementation remains read-only in this task, so the application refinement is temporarily ahead of it.

| Token | Value | Use |
| --- | --- | --- |
| Field | `#0B1A16` | ground |
| Lit | `#163429` | the lit region, upper left |
| Deep | `#06100D` | lower right, footer |
| Ink | `#EAF3EE` | text |
| Body | `#C3D6CD` | running text |
| Muted | `#A7C0B4` in the app; `#8FB0A3` on the studio page pending a later pass | labels, captions |
| Rule | `rgba(234,243,238,.14)` | hairlines |
| Phosphor core | `#C9FFE1` | the trace's core, the one lit word; light only |
| Phosphor | `#6FF0A8` | the glow; light only |
| Phosphor deep | `#1E8F5E` | links on paper |
| Graticule | `#9CFFC4` at 13% | 40 px hairline squares, one zone |

**In the tool since 2026-09-20:** `dashboard/src/identity.css` carries these tokens and the three faces (self-hosted under the OFL in `dashboard/public/fonts/`); `dashboard/src/Mark.tsx` draws the mark from the same path; the graticule sits in the header zone only; the phosphor is the trace under the header and the one lit word, *live*, when Sentinel returned its latest Health reading; level markers in the record are monochrome glyphs. The nearby readout states the bridge outcome, so a reachable app does not imply Windows answered. Change a value here first, then update the application. A later studio task can bring the page into alignment.

Type: Azeret Mono 300 for headlines and section titles, 400 tracked capitals for the wordmark (OFL; the Latin subset is self-hosted on the site); JetBrains Mono for readouts and labels; DM Sans for text. Mark: `S_PATH` on a 64-unit grid (`M45 19C42 12 22 11 21 20C20 29 44 30 44 41C44 50 23 53 18 45`), the beam's dot at (18, 45), on a tile of radius 14 carrying an 8-unit graticule; the tab icon drops the graticule and the glow. The trace: flat, one pulse at the right, drawn once on load. The headline on the page: *Inspect, select, compose.*, David's choice on 2026-09-20, the last word lit; the lede opens with his line *A stethoscope for your computer.* The figure is his own machine's System-log record around one evening's two freezes, published as relative minutes with provider, ID and level only and no date, so it never ages. Amber (`#FFE4B0`, `#FFB454`, `#A8651A`) is recorded as the accent he did not choose.

David's five ChatGPT renders from the prompts (backdrop, graticule, trace, share-card ground, app icon) are references only, kept outside this repository. The application takes these values back when it is next worked on: its `globals.css` tokens, the mark, the display face.

## Original studio decisions (historical, 2026-09-20)

The sections below preserve the design board's source material and alternatives. Product capabilities and current screen names are owned by the live source and `docs/CLAIMS.md`; this older studio record is not a current feature list.

- The name, and the public label **Windows application**.
- The copy: present tense, source-backed, arguing from capability. No live telemetry, prediction, diagnosed machine or product release claimed (`mainthread-studio/docs/mainthread/evidence/SYSTEM-SENTINEL.md`).
- The four sources it reads (hardware, system events, driver history, crash records) and the verbs (inspect, select, compose).
- The page's four story sections, its date (2026-09-09) and its place after Zoning Signal.

## Original board questions (historical, 2026-09-20)

1. **The character.** Three directions below; A is recommended.
2. **The headline.** The current line, *Bring the evidence together.*, is plain and positive and every panel carries it. Offered, not recommended: *Inspect, select, compose.* and *The record, read back.*
3. **The figure's data.** Default: a schematic captioned as one, built from Windows' own providers and event IDs and the detector's defaults. If David authorizes his own machine's record (dates and event IDs from the March–May sessions only; no serials, paths or message text), the figure becomes real and to scale.
4. **The application.** Its palette is `create-next-app`'s zinc and blue-600 (`frontend/app/globals.css`), Geist by default. The identity is decided on the page and can flow back into the app when it is finished.

## The subject, as the board reads it

A Windows computer freezes, you reboot, and the only witness is the log. The product gathers that record, lets a person choose what matters, and carries it into the conversation. The figure is the composer's most distinctive mechanism: the events that came *before* a chosen record (`frontend/components/context/ContextComposer.tsx`, `backend/services/system_logs.py:get_events_prior_to_timestamp`).

Vocabulary used on the board, all public: Kernel-Power 41, EventLog 6008, Kernel-General 12, WHEA-Logger 17/18/19, Display 4101, storahci 129. Detector defaults from `backend/services/whea/storms.py`: 60-second buckets, burst at 5/min (critical above 10/min), acceleration at 2× a 240-bucket baseline, over a 10-bucket recent window. Evidence classes raw/derived/invariant/inferred (`ContextStore.ts`); signal classes suppressions/gaps/pressure/transitions/mismatches (`services/domains/forensic_signals.py`).

## A. Instrument (recommended)

The page is the instrument's screen after dark: a satin deep green field, a graticule in one zone (upper right), one phosphor trace as the light, the record read back against it in a monospaced readout.

| Token | Value | Use |
| --- | --- | --- |
| Field | `#0B1A16` | page ground |
| Lit | `#163429` | the lit region, upper left |
| Deep | `#06100D` | lower right, footer |
| Ink | `#EAF3EE` | text |
| Muted | `#8FB0A3` | labels |
| Rule | `rgba(234,243,238,.14)` | hairlines |
| Phosphor core | `#C9FFE1` | light only, one lit word |
| Phosphor | `#6FF0A8` | light only (glow) |
| Phosphor deep | `#1E8F5E` | links on paper |
| Amber core / amber / deep | `#FFE4B0` / `#FFB454` / `#A8651A` | the alternative accent (P3) |

Graticule: 40 px hairline squares in `#9CFFC4` at 13% opacity, masked to one zone. Type: **Azeret Mono 300** for the headline and the wordmark (OFL; self-host at build), JetBrains Mono for readouts, DM Sans for text. Mark: the S drawn as one continuous trace (`S_PATH` in the build script, 64-unit box) with a bright dot at its end, on a tile carrying a faint graticule; the same path is the hero ribbon and the figure's line weight. One motion: the trace draws once on load, static under reduced motion.

Why: an instrument is what the product is; material and light as David asked; distinct from the four dark surfaces on the site (indigo field, charcoal bench, plum plate, black athletic) in hue and kind; the graticule is an instrument's grid, not Multithread's cutting-mat plus-grid. Watch: green on dark drifts toward the terminal cliché; the satin field, one trace and off-white text keep it an instrument.

## B. Record

The page is the printed record: cool paper under a raking light, ruled like a log book, the events typeset in IBM Plex Mono with the ones carried into context tagged in exhibit red. The only light entry among the dark ones.

Paper `#EDF0F2`, lit `#F9FAFB`, shade `#DCE2E7`, ink `#151A1F`, muted `#66717C`, rule `#C6CFD6`, exhibit red `#D42B2B`, deep `#9B1B1B`. Type: Newsreader (opsz 72) display, IBM Plex Mono record and wordmark, DM Sans text. Mark: an exhibit tag with a punched hole and the S. Figure: the same record as a ledger. Watch: paper reads flat unless the light and ruling carry material; Lichtenberg's page is light already; this is the drawing-set instinct David passed on for Bendr.

## C. Thermal

The page is a thermal image: near-black, one ironbow ribbon of heat as the only light, the record as a heat strip. Field `#08070B`, lit `#1B0F2E`, ink `#F3EFF7`, muted `#9B90B3`; ironbow `#12003A → #5B0F8C → #C21E7A → #F0552A → #FFA630 → #FFF1B8`, used only as a gradient. Type: Martian Mono 200 display. Mark: the same S path stroked with the ramp. Watch: the most decorative; its violets and magentas sit near Zoning Signal's plum and rose gold; heat implies a thermal cause the page must not claim.

## After a direction is named

1. Record the selected identity here (a "Selected identity" section owns the values).
2. In the studio: the page and homepage entry as a surface end to end (`data-surface`, self-hosted display face, mark component, the figure computed from the vocabulary and the detector's constants, share card and tab icon from the same code, one motion), verified at 390 and 1440, deployed, byte-verified, recorded in RELEASE and DECISIONS; the evidence note updated.
3. Later, the application takes the identity back: tokens, mark, display face.

## Provenance

Read for this note, 2026-09-20: `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `frontend/app/globals.css`, `frontend/app/layout.tsx`, `frontend/components/{Sidebar,GlassCard}.tsx`, `frontend/components/context/{ContextStore.ts,ContextComposer.tsx}`, `frontend/components/views/WheaStormView.tsx`, `frontend/components/widgets/WidgetSiliconHealth.tsx`, `backend/main.py` (routes), `backend/services/{system_logs.py,cper_decoder.py}`, `backend/services/whea/storms.py`, `backend/services/domains/forensic_signals.py`, `backend/collectors/events.py`, the head of `system_prompts.md`, and the opening of one private session stream for vocabulary only. No service ran; nothing from the private streams appears on the board or is proposed for the page without David's authorization.
