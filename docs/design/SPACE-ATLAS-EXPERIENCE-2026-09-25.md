# Space Atlas: the navigable world

Status: product direction approved by David after his critique and two high-effort Opus 5.5 design passes, Buddy's API critique, and browser review of the [interactive concept](space-atlas-prototype/index.html). David wants every idea in this catalogue pursued as the product grows. The concept uses invented values. The dashboard now has real Windows-backed top-level and scoped measurements, exact continuation pages and a shared tile/planet navigator, but no retained hierarchy snapshot across levels yet.

## The decision

Make the atlas and the planet two projections of **one measured hierarchy**. At every level, the person's current scope, selected node, ranked siblings, coverage, scan time and route home are the same in both. A third archaeology lens makes the descent itself visible: a stack of observed ancestor scopes with the current level open. Switching views changes the visual grammar, not the data or location. A file is a leaf that opens an evidence plaque; it never pretends to contain another world.

The traditional tile atlas is a primary instrument, not a fallback. It shows every region at once and lets area comparisons remain legible. The globe makes the hierarchy memorable and tactile, but its hidden hemisphere and edge foreshortening require an always-visible full-surface key and ranked list. Exact bytes live in the shared inspector and API. The present prototype's planet is built by wrapping the tile coordinates onto a sphere through longitude and `sin(latitude)`, an equal-area surface mapping. It stops rendering when idle. This mapping is a first implementable step; a Lambert azimuthal equal-area disk with measured/free/unattributed rings is a promising later experiment, not yet a shipped decision.

## Ideas worth prototyping

| Idea | Distinct interaction | Useful question | Data gate and failure mode |
| --- | --- | --- | --- |
| **Terra Cognita** | A survey fills measured land; volume-used but unattributed space stays visibly uncharted | What do we know, and where does the scan stop? | Scan progress must really stream before land can appear while scanning. Fog is not junk or a folder. |
| **Core Sample** | Pull a vertical slice from a selected region to see its internal strata | What is inside this folder, and at which depth? | Hierarchy strata can use measured child totals now. Age strata require byte-weighted last-write bands; old means last written, not unused. |
| **Tideline** | Scrub dated shorelines of free and used space | When did pressure rise, and what changed? | Requires comparable retained scans. Gaps must remain gaps; never draw an invented trend through them. |
| **Matter lens** | Show local allocation as solid fill and logical length as an outline | Why does a large-looking file occupy little local space? | Requires per-node logical/allocated aggregates. Cloud placeholder deletion may affect a cloud copy; an outline is not a cleanup hint. |
| **Watershed** | Follow widening branches upstream through the hierarchy | Which child accounts for this folder's footprint? | Channel width maps to allocated bytes. A multitude of small files can matter more than one wide branch. |
| **Skyline** | Folder parcels rise with file count while their footprint remains proportional to bytes | Where are millions of tiny files? | Requires reliable counts. Height easily overwhelms area perception, so top-down tiles remain the comparison view. |
| **Regrowth Reef** | Revisit a previously cleared cache and see its observed regrowth | Which space will return soon? | Requires dated before/after scans and a known cleanup event. A warm cache can be useful. |
| **Orbits** | Place folders at a radius based on byte-weighted last-write age | Which material was last written recently? | Current age bands support a coarse form. A single newly written file must not make a whole folder look recently used. |
| **Pressure Map** | Show measured growth and, later, churn as weather over the same regions | What is growing fastest, and what is changing repeatedly? | Two snapshots yield net growth, not churn. Churn needs a reliable change feed; missing intervals stay blank. |
| **Metabolism** | Trace observed transfers or moves between regions | Where does incoming material settle? | Requires a change journal or explicit events. Matching a source and destination is an inference unless an identity-preserving move is observed. |
| **Packing Crate** | Compare selected material with a target drive or backup limit | What might fit elsewhere? | Filesystem, compression and cluster size change allocation; show a range, never a guarantee. |
| **Twin Stars** | Show independently stored files with matching content hashes | Which copies might be duplicates? | Hashing is expensive and reads content; hardlinks and intentional backups must be distinguished. It requires a separately approved, bounded scan mode. |
| **Resonance** | Play region shares as proportional durations | Can someone hear the rough composition? | Optional, off by default. Duration is an orientation aid, not an exact substitute for the list. |

Do **not** build a fun deletion vortex or an automatic “junk storm.” They make destruction feel like the reward and suggest measured savings before any filesystem change is observed.

## Delivery sequence for all ideas

The ideas form one navigable instrument rather than thirteen disconnected demos. Each new lens reads the same scope and selection, and each can be disabled when its evidence is unavailable.

1. **Survey and descent:** Terra Cognita, the exact tiles, the globe, archaeology strata and Watershed. These use the current allocated/logical totals, coverage and child handles. The page-through tail now reveals exact omitted rows from one walk. A retained hierarchy snapshot across levels is still needed before a branch can feel instantaneous and reconcile at every depth.
2. **One-scan composition:** Matter, coarse Orbits, Core Sample and Skyline. Logical allocation, last-write age bands and file counts are already collected; the UI must keep cloud, compression, hardlink and last-use caveats visible. Finer-grained age and file-type strata require additional aggregates.
3. **History and verification:** Tideline, net-growth Pressure Map, Regrowth Reef and receipts. They require retained comparable snapshots, scan IDs, a stable node identity policy and explicit action markers. Show “no observation” rather than a forecast when those are absent.
4. **Special investigations:** Packing Crate, Metabolism, Twin Stars and churn weather. These need separate inputs: target filesystem/drive facts, reliable change events, and opt-in bounded content hashing. They must declare cost, privilege needs and inferential joins before the UI offers a conclusion.
5. **Alternative access:** Resonance is an optional sonification of the same measured shares and never replaces the list, keyboard path or exact readout.

## Three layers of a flagship experience

1. **Survey:** a volume ledger reconciles capacity, free, attributed allocation and the remainder to used space. Unknown or unreadable folders carry no invented area. Partial folders show `≥` and a distinct border/texture. The current scanner can do this for the selected walk, with the caveat that a scoped child is a fresh independent observation.
2. **Descend:** select inspects in place; Enter or an explicit button requests the child; Back and breadcrumbs restore an ancestor. The tile atlas, planet and archaeology strip share that route. The current API's `scope_id` enables real on-demand descent, but handles expire and separate scopes can re-attribute hardlinks. Persistent scan IDs and node IDs are needed for a smooth, fully reconciled snapshot.
3. **Explain and verify:** an agent can cite the exact reading, coverage, largest observed files and reasons a hypothesis is only a lead. Once snapshots exist, a receipt compares an action's predicted effect with *observed* free-space and subtree changes. A move into the Recycle Bin may change attribution without changing free space; other delete paths behave differently. No candidate is called safe from its name, size or age alone.

## One navigation grammar

| Action | Tiles | Planet | Archaeology | Shared effect |
| --- | --- | --- | --- | --- |
| Select | Outline a tile | Outline and orient to a territory | Highlight a stratum | Inspector updates; no navigation |
| Enter | Tile becomes the current surveyed scope | Territory fills the next view | Add the next measured stratum | Request child `scope_id`; preserve ancestor and selection |
| Ascend | Restore prior tile layout | Restore prior orientation | Remove one stratum | Same parent and focus in all views |
| Switch view | Preserve selected tile | Preserve selected territory | Preserve selected stratum | Scope, bytes, coverage and timestamp stay fixed |
| Leaf | File plaque | File plaque | Terminal layer | Stop zooming; show evidence and limits |

On a phone, the ranked list and breadcrumb must remain usable without a gesture. With reduced motion, descent is an immediate state change with a spoken level announcement. Nothing rotates or redraws while idle. Keyboard inspection and descent use the same path as pointer and touch.

## Encoding rules

- Area means observed allocated bytes within the selected walk. A partial node's area is its measured lower bound. An unmeasured node is a marker/list row with no area. The full-surface key states the denominator.
- Colour identifies a region in the first release. When comparable snapshots exist, change can become a separate explicit lens; never silently reuse the same colour to mean risk or cleanup safety.
- `≥` and a single coverage texture mean lower-bound measures. A separate uncharted volume region means used bytes not attributed by the selected walk; it is not a directory and has no Enter action.
- Elevation, if tried, must name its metric (for example file count). It must not suggest greater bytes than the corresponding parcel area.
- A scan timestamp and method stay beside every measurement. Two separate scoped takes are not one snapshot. A depth transition may refresh values, and the interface must say so.

## API as the source of truth

The [data contract](SPACE-ATLAS-DATA-CONTRACT.md) defines the observation, comparison and evidence gates each lens needs.

The current `space` reading is already catalogued through the HTTP API and MCP. It offers bounded walks, immediate folder groups, logical and allocated bytes, coverage, hardlink repeat counts, placeholder/compression counts, byte-weighted last-write age bands, a volume reconciliation, up to 20 metadata-only largest files, and opaque short-lived child handles. Names and relative file paths are opt-in; no caller-supplied path is accepted. The largest-file list is a lead list, not a deletion list. Last-write age does not establish last use.

The durable contract still needs:

1. A **scan ID** and stable volume-plus-file identity for every node. A 64-bit directory ID may not be adequate on every Windows filesystem; verify the extended 128-bit API before promising rename-stable matching.
2. One **retained hierarchy snapshot** whose children reconcile with their parent under the same hardlink attribution policy and time boundary. Current scoped rescans do not.
3. A **measurable tail inside the map** as pages open. Exact omitted rows and their child handles are now available through `space_page`, but the live map retains a single folded territory rather than reflowing each page's area.
4. Comparable snapshots and growth deltas with explicit missing/renamed/re-attributed states, plus retention controls.
5. Type summaries and classification provenance only where measured. Byte-weighted last-write bands are now returned, but a modified timestamp is not evidence of last use.
6. An agent-facing evidence reference that names the reading, scan, node, measurement basis and coverage; candidate and review sections must remain inferred, with the rule that produced each claim.

The visual tests are simple to falsify: no rendered child without a reading; no area for a null measure; tile and planet agree on region shares; the full-surface key includes hidden regions; keyboard-only descent reaches every enterable folder; returning to an ancestor restores selection; a stale handle fails visibly; idle rendering stops. Real Windows tests cover compressed allocation, hardlinks, junctions, denied children, a mid-directory cap, encoded child paths and selected-root reparse refusal. The dashboard browser fixture now uses an actual `space` envelope shape for desktop, phone, selection, descent and folded-tail paging; a live authenticated dashboard check remains.

## Consultation notes

Opus's first high-effort pass identified the pole-to-pole lune and small-end-band problems, plus hidden-hemisphere distortion and perpetual redraw. The prototype now uses continuous regions, exact ranked values, a tile-wrapped globe and on-demand frames. Its equal-area disk proposal remains a serious alternate layout. Opus's second pass produced the concepts above and the useful principle that each astonishing interaction should answer one named user question. Buddy emphasized exact node evidence, scoped drill-down, pagination and a visible blind area. Buddy also suggested inferred review candidates from top-level names; that is too early: size, age and a path rule do not establish safe deletion or even freeable bytes. These are design inputs assessed against the current code and host tests, not implementation evidence by themselves.
