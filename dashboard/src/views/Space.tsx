import { CSSProperties, KeyboardEvent, PointerEvent, ReactNode, RefObject, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { AddToStack } from '../AddToStack';
import { Unauthorized, observed, take, type Cls, type Reading } from '../api';
import { OutcomeLine, clock } from '../Outcome';
import { Facts, Head, SectionHead, Segmented, Tree, basisOf, part, size } from '../Sections';
import { useApp, type SpaceLevel, type SpaceMode, type SpaceViewState } from '../store';
import { useReading, type Taken } from '../useReading';
import styles from './Space.module.css';

/**
 * Space: where a folder's space is, one bounded walk at a time.
 *
 * Nothing is walked until the person asks. Every level of the walk path is its own walk of its own
 * scope, held in this tab; returning to an ancestor shows the answer that walk gave, and entering a
 * folder asks the machine again rather than slicing the parent. Tiles and Planet are two drawings
 * of one layout: the same rectangles, proportional to allocated bytes, laid flat or wrapped onto
 * a sphere by longitude and sin(latitude), which keeps every region's share of the surface. The
 * ranked list under both carries every exact figure, including the far side of the planet, and
 * rows without a measure are listed with their reason and never given an area.
 */

const WALK = { seconds: 30, max_entries: 1_000_000, include_names: true };
/** The tile map's aspect, matched by .map in Space.module.css at the same breakpoint. */
const WIDE = 16 / 9;
const NARROW = 10 / 11;
const WIDE_QUERY = '(min-width: 640px)';

type Kind = 'directory' | 'top_level_files' | 'other_groups';
type Status = 'complete' | 'partial' | 'skipped' | 'not_scanned' | 'not_listed';
type SkipKind = 'mount_point' | 'symlink' | 'cloud' | 'other';

interface SpaceGroup {
  kind: Kind;
  label: string | null;
  name: string | null;
  scope_id?: string | null;
  file_id_64?: string | null;
  skipped: SkipKind | null;
  status: Status;
  /** Only on the folded row: how many rows it sums. */
  groups?: number;
  files: number | null;
  directories: number | null;
  logical_bytes: number | null;
  allocated_bytes: number | null;
  link_repeats: number | null;
  placeholder_files: number | null;
  placeholder_logical_bytes: number | null;
  compressed_or_sparse_files: number | null;
  reparse_files: number | null;
  last_write_age_allocated_bytes: Record<string, number> | null;
  skipped_directories: Record<string, number>;
  unreadable_directories: Record<string, number>;
  cross_volume: number;
  visited_directories: number;
  unvisited_directories: number;
}

interface Totals { allocated_bytes: number; logical_bytes: number; complete: boolean; lower_bound: boolean }

interface Coverage {
  complete: boolean;
  stopped: 'time_limit' | 'entry_limit' | null;
  unvisited_directories: number;
  skipped_directories: Record<string, number>;
  unreadable_directories: Record<string, number>;
  cross_volume_directories: number;
  link_repeats: number;
  unidentified_files: number;
}

interface Reconciliation { volume_used_bytes: number | null; scan_allocated_bytes: number; scan_lower_bound: boolean; not_attributed_bytes: number | null }

interface Collection {
  root: 'home' | 'selected_scope';
  volume: { name: string; file_system: string; total_bytes: number; free_bytes: number; available_bytes: number } | null;
  placeholders_exposed: boolean;
  limits: { seconds: number; max_entries: number } | null;
  elapsed_ms: number | null;
  entries: number | null;
  error_codes: Record<string, number>;
}
interface Pagination { scan_id: string; total_groups: number; returned_groups: number; folded_groups?: number; next_page_id: string | null }

interface Example { reason: string; path: string }

/** One row of the walk as both drawings and the list see it. */
interface Row {
  key: string;
  g: SpaceGroup;
  name: string;
  /** Allocated bytes; null when the row was not listed and has no measure. */
  bytes: number | null;
  partial: boolean;
  tail: boolean;
  fromTail: boolean;
  enterable: boolean;
  /** Place among measured rows, largest first; null for unmeasured rows. */
  rank: number | null;
}

/** A measured row's rectangle, normalized to the unit square. Tiles and Planet draw these same numbers. */
interface Tile { row: Row; x: number; y: number; w: number; h: number }

// ---------------------------------------------------------------------------
// The navigator: one walk path, one selection per level, one mode
// ---------------------------------------------------------------------------

function freshLevel(scopeId: string, from: Pick<SpaceLevel, 'name' | 'label' | 'entered'>): SpaceLevel {
  return { scopeId, ...from, reading: null, pages: [], pageAsking: false, pageProblem: null,
    failure: null, problem: null, asking: true, request: 1, restored: false, selected: null };
}

function withCurrent(view: SpaceViewState, change: (level: SpaceLevel) => SpaceLevel): SpaceViewState {
  if (!view.levels.length) return view;
  const levels = view.levels.slice();
  levels[levels.length - 1] = change(levels[levels.length - 1]);
  return { ...view, levels };
}

function keyOf(g: SpaceGroup, index: number): string {
  if (g.kind === 'top_level_files') return 'files';
  if (g.kind === 'other_groups') return 'folded';
  if (g.file_id_64) return `id:${g.file_id_64}`;
  if (g.scope_id) return `scope:${g.scope_id}`;
  return g.name !== null ? `dir:${g.name}` : `dir#${index}`;
}

const groupsOf = (reading: Reading | null): SpaceGroup[] => part<SpaceGroup[]>(reading, 'groups') ?? [];

/** Record the answer a request received, on the level that asked, if it still asks. */
function settleWalk(view: SpaceViewState, depth: number, request: number, answer: { reading: Reading } | { problem: string }): SpaceViewState {
  const level = view.levels[depth];
  if (!level || depth !== view.levels.length - 1 || !level.asking || level.request !== request) return view;
  return withCurrent(view, (current) => {
    if ('problem' in answer) return { ...current, asking: false, problem: answer.problem, failure: null };
    if (!observed(answer.reading)) return { ...current, asking: false, problem: null, failure: answer.reading };
    const keys = new Set(groupsOf(answer.reading).map(keyOf));
    return { ...current, asking: false, problem: null, failure: null, reading: answer.reading,
      pages: [], pageAsking: false, pageProblem: null, restored: false,
      selected: current.selected && keys.has(current.selected) ? current.selected : null };
  });
}

/** OutcomeLine's contract, drawn from the held level rather than from one request. */
function levelTaken(level: SpaceLevel, params: Record<string, string | number | boolean>, retake: () => void): Taken<unknown> {
  const reading = level.reading ?? level.failure;
  return {
    state: level.asking ? 'taking' : level.problem ? 'lost' : reading ? 'done' : 'idle',
    reading,
    problem: level.problem,
    held: level.reading !== null && (level.restored || level.asking || level.failure !== null || level.problem !== null),
    latestFailure: level.reading ? level.failure : null,
    requestedParams: params,
    retake,
  };
}

export function Space() {
  const levels = useApp((s) => s.spaceView.levels);
  const mode = useApp((s) => s.spaceView.mode);
  const setSpaceView = useApp((s) => s.setSpaceView);
  const depth = levels.length - 1;
  const level = depth >= 0 ? levels[depth] : null;
  const params = { ...WALK, scope_id: level?.scopeId ?? '' };
  const asking = Boolean(level?.asking);
  const request = level?.request ?? 0;
  const live = useReading<unknown>('space', params, asking);
  // A rescan of the same scope first renders the previous answer; only an answer that arrives
  // after this request was seen in flight belongs to it.
  const armed = useRef<number | null>(null);

  useEffect(() => {
    if (!asking) { armed.current = null; return; }
    if (live.state === 'taking') { armed.current = request; return; }
    if (armed.current !== request) return;
    const reading = live.reading;
    if (live.state === 'done' && reading) setSpaceView((v) => settleWalk(v, depth, request, { reading }));
    else if (live.state === 'lost') setSpaceView((v) => settleWalk(v, depth, request, { problem: live.problem ?? 'the request failed' }));
  }, [asking, request, depth, live.state, live.reading, live.problem, setSpaceView]);

  // Leaving the view drops a walk in flight: its answer has nowhere to land, and taking it again
  // on return would repeat a heavy walk nobody asked for twice. The level returns to what it held.
  useEffect(() => () => {
    useApp.getState().setSpaceView((v) => ({ ...v, levels: v.levels.map((l) => (l.asking ? { ...l, asking: false } : l)) }));
  }, []);

  const scanHome = () => setSpaceView((v) => ({ ...v, levels: [freshLevel('', { name: null, label: null, entered: null })] }));
  const rescan = () => setSpaceView((v) => withCurrent(v, (l) => (l.asking ? l : { ...l, asking: true, request: l.request + 1 })));
  const select = (key: string) => setSpaceView((v) => withCurrent(v, (l) => ({ ...l, selected: key })));
  const setMode = (next: SpaceMode) => setSpaceView((v) => ({ ...v, mode: next }));
  const goTo = (index: number) => setSpaceView((v) => ({ ...v,
    levels: v.levels.slice(0, index + 1).map((l, i) => (i === index ? { ...l, restored: l.reading !== null } : l)) }));
  const enter = (row: Row) => {
    const scopeId = row.g.scope_id;
    if (!row.enterable || !scopeId || !level?.reading || level.asking) return;
    const at = level.reading.asked_at;
    setSpaceView((v) => {
      const next = withCurrent(v, (l) => ({ ...l, selected: row.key }));
      return { ...next, levels: [...next.levels, freshLevel(scopeId, { name: row.g.name, label: row.g.label,
        entered: { bytes: row.bytes, lowerBound: row.partial, at } })] };
    });
  };

  const busy = asking;
  const scanLabel = busy ? 'Walking…' : depth === 0 ? (level?.reading ? 'Scan Home again' : 'Scan Home')
    : level?.reading ? 'Walk this folder again' : 'Walk this folder';
  const shown = level?.reading && observed(level.reading) ? level.reading : null;
  const initialPage = part<Pagination>(shown, 'pagination');
  const lastPage = level?.pages.length ? part<Pagination>(level.pages[level.pages.length - 1], 'pagination') : null;
  const nextPageId = (lastPage ?? initialPage)?.next_page_id ?? null;
  const loadMore = () => {
    if (!level?.reading || !nextPageId || level.pageAsking) return;
    const scanId = initialPage?.scan_id;
    const at = depth;
    setSpaceView((v) => withCurrent(v, (l) => ({ ...l, pageAsking: true, pageProblem: null })));
    take('space_page', { page_id: nextPageId, include_names: true })
      .then((answer) => setSpaceView((v) => {
        const current = v.levels[at];
        if (!current || part<Pagination>(current.reading, 'pagination')?.scan_id !== scanId) return v;
        const levels = v.levels.slice();
        levels[at] = observed(answer)
          ? { ...current, pages: [...current.pages, answer], pageAsking: false, pageProblem: null }
          : { ...current, pageAsking: false, pageProblem: answer.error?.detail ?? 'The page did not answer.' };
        return { ...v, levels };
      }))
      .catch((error: unknown) => {
        if (error instanceof Unauthorized) useApp.getState().setSession('closed');
        setSpaceView((v) => {
        const current = v.levels[at];
        if (!current || part<Pagination>(current.reading, 'pagination')?.scan_id !== scanId) return v;
        const levels = v.levels.slice();
        levels[at] = { ...current, pageAsking: false, pageProblem: error instanceof Error ? error.message : String(error) };
        return { ...v, levels };
        });
      });
  };

  return (
    <section>
      <Head title="Space">
        {shown ? <Segmented label="Drawing" value={mode} onChange={setMode} options={[{ value: 'tiles', label: 'Tiles' }, { value: 'planet', label: 'Planet' }]} /> : null}
        {level ? <button className={styles.scan} onClick={rescan} disabled={busy}>{scanLabel}</button> : null}
      </Head>
      <p className={styles.lede}>
        Where your home folder&rsquo;s space is, walked only when you ask. Tiles and Planet draw the same areas; the list keeps every exact share. Folder names appear after you request a walk.
      </p>
      <p className={styles.labLink}><a href="/space-atlas-lab/" target="_blank" rel="noopener noreferrer">Explore the sixteen-lens interaction lab <span aria-hidden="true">↗</span></a> · synthetic examples, separate from this machine&rsquo;s readings</p>
      {!level ? <Offer onScan={scanHome} /> : (
        <>
          <WalkPath levels={levels} onGo={goTo} />
          <div className={styles.outcome}>
            <OutcomeLine taken={levelTaken(level, params, rescan)} noun="rows" singular="row" emptyText="Nothing in this folder" />
            {level.reading ? <AddToStack item={{ kind: 'reading', envelope: level.reading }} label="Stack this walk (with folder names)" /> : null}
          </div>
          {depth > 0 ? <Separate level={level} parent={levels[depth - 1]} /> : null}
          {!level.reading && !level.asking && !level.failure && !level.problem ? (
            <p className={styles.quiet}>{depth > 0 ? 'This folder has not been walked in this tab. Its handle lasts 30 minutes from the parent walk.' : 'Home has not been walked in this tab.'}</p>
          ) : null}
          {depth > 0 && !level.reading && (level.failure || level.problem) ? (
            <p className={styles.quiet}>
              <button className={styles.textButton} onClick={() => goTo(depth - 1)}>Back to {levels[depth - 1].name ?? 'Home'}</button>
              {' '}and walk it again for fresh handles if this one expired.
            </p>
          ) : null}
          {shown ? (
            <Atlas reading={shown} level={level} mode={mode} busy={busy} scoped={depth > 0} onLoadMore={loadMore} hasMore={Boolean(nextPageId)}
              onSelect={select} onEnter={enter} onUp={depth > 0 ? () => goTo(depth - 1) : null} />
          ) : null}
        </>
      )}
    </section>
  );
}

function Offer({ onScan }: { onScan: () => void }) {
  return (
    <div className={styles.offer}>
      <p>
        <strong className={styles.strong}>Scan Home</strong> walks your home folder once, for up to 30 seconds or 1,000,000 entries. It reads each
        folder&rsquo;s own listing, so no file is opened, and links, mount points and cloud folders are counted but not followed.
      </p>
      <p>
        Folder names come back to this page because you asked for them; they never enter the address bar. A walk that stops early shows lower
        bounds, marked <span className="readout">≥</span>. Entering a folder walks that folder on its own.
      </p>
      <button className={styles.scanLead} onClick={onScan}>Scan Home</button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The walk path: each ancestor a held walk of its own, the current one last
// ---------------------------------------------------------------------------

function levelSummary(level: SpaceLevel): string {
  if (level.asking && !level.reading) return 'walking…';
  if (!level.reading) return level.failure || level.problem ? 'not observed' : 'not walked';
  const totals = part<Totals>(level.reading, 'totals');
  const figure = totals ? `${totals.lower_bound ? '≥ ' : ''}${size(totals.allocated_bytes)}` : '';
  return `${figure} · walked ${clock.format(new Date(level.reading.asked_at))}${level.asking ? ' · walking again…' : ''}`;
}

function WalkPath({ levels, onGo }: { levels: SpaceLevel[]; onGo: (index: number) => void }) {
  const depth = levels.length - 1;
  return (
    <nav className={styles.path} aria-label="Walk path">
      <p className={`${styles.pathLabel} label`}>Walk path · each level is its own walk</p>
      <ol className={styles.pathList}>
        {levels.map((level, index) => {
          const name = level.name ?? 'Home';
          const body = (
            <>
              <span className={styles.crumbName}>{name}</span>
              <span className={`${styles.crumbMeta} readout`}>{levelSummary(level)}</span>
            </>
          );
          return (
            <li key={`${index}:${level.scopeId}`} className={styles.pathItem}>
              {index < depth ? (
                <button className={styles.crumb} onClick={() => onGo(index)} aria-label={`Return to ${name}, held walk: ${levelSummary(level)}`}>{body}</button>
              ) : (
                <span className={`${styles.crumb} ${styles.crumbHere}`} aria-current="location">{body}</span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** A child scope beside the row it was entered from: two walks, stated as two. */
function Separate({ level, parent }: { level: SpaceLevel; parent: SpaceLevel }) {
  const entered = level.entered;
  const enteredBytes = entered ? entered.bytes : null;
  const totals = level.reading ? part<Totals>(level.reading, 'totals') : null;
  const from = parent.name ?? 'Home';
  return (
    <p className={styles.separate}>
      {entered && enteredBytes !== null ? (
        <>The {from} walk of {clock.format(new Date(entered.at))} put this folder&rsquo;s row at <span className="readout">{entered.lowerBound ? '≥ ' : ''}{size(enteredBytes)}</span>. </>
      ) : <>Entered from the {from} walk. </>}
      {level.reading && totals ? (
        <>This walk, taken {clock.format(new Date(level.reading.asked_at))}, measured <span className="readout">{totals.lower_bound ? '≥ ' : ''}{size(totals.allocated_bytes)}</span>. </>
      ) : null}
      Each walk counts a hard-linked file once, where it meets it first, so the two figures need not agree; neither is a change over time.
    </p>
  );
}

// ---------------------------------------------------------------------------
// The atlas: one layout, two drawings, one list
// ---------------------------------------------------------------------------

const SKIP_WORDS: Record<string, string> = { mount_point: 'mount point', symlink: 'symbolic link', cloud: 'cloud', other: 'unrecognized reparse point' };
const UNREADABLE_WORDS: Record<string, string> = { denied: 'refused', vanished: 'vanished', other: 'other error' };

function nameOf(g: SpaceGroup, index: number): string {
  if (g.kind === 'top_level_files') return 'Files directly in this folder';
  if (g.kind === 'other_groups') return `${g.groups ?? 'Other'} smaller rows, folded`;
  return g.name ?? g.label ?? `Folder ${index + 1}`;
}

function toRow(g: SpaceGroup, index: number): Row {
  const bytes = typeof g.allocated_bytes === 'number' ? g.allocated_bytes : null;
  return { key: keyOf(g, index), g, name: nameOf(g, index), bytes, partial: bytes !== null && g.status !== 'complete',
    tail: g.kind === 'other_groups', fromTail: false,
    enterable: g.kind === 'directory' && typeof g.scope_id === 'string' && g.scope_id !== '', rank: null };
}

/**
 * Squarified treemap (Bruls, Huizing and van Wijk) in a box of the given aspect, returned in unit
 * coordinates. Area is proportional to bytes; a row without a measure is never passed in.
 */
function squarify(rows: Row[], aspect: number): Tile[] {
  const items = rows.filter((r) => (r.bytes ?? 0) > 0);
  const total = items.reduce((sum, r) => sum + (r.bytes ?? 0), 0);
  if (!total) return [];
  const areas = items.map((row) => ({ row, a: ((row.bytes ?? 0) / total) * aspect }));
  const out: Tile[] = [];
  let x = 0, y = 0, w = aspect, h = 1;
  const sum = (list: typeof areas) => list.reduce((s, it) => s + it.a, 0);
  const worst = (list: typeof areas, side: number) => {
    const s = sum(list);
    return list.reduce((max, it) => Math.max(max, (side * side * it.a) / (s * s), (s * s) / (side * side * it.a)), 0);
  };
  const place = (list: typeof areas) => {
    const s = sum(list);
    if (w >= h) {
      const cw = s / h;
      let cy = y;
      for (const it of list) { const ih = it.a / cw; out.push({ row: it.row, x, y: cy, w: cw, h: ih }); cy += ih; }
      x += cw; w -= cw;
    } else {
      const rh = s / w;
      let cx = x;
      for (const it of list) { const iw = it.a / rh; out.push({ row: it.row, x: cx, y, w: iw, h: rh }); cx += iw; }
      y += rh; h -= rh;
    }
  };
  let row: typeof areas = [];
  for (let i = 0; i < areas.length;) {
    const side = Math.min(w, h);
    const candidate = [...row, areas[i]];
    if (!row.length || worst(candidate, side) <= worst(row, side)) { row = candidate; i += 1; }
    else { place(row); row = []; }
  }
  if (row.length) place(row);
  return out.map((t) => ({ ...t, x: t.x / aspect, w: t.w / aspect }));
}

function buildModel(groups: SpaceGroup[], aspect: number) {
  const rows = groups.map(toRow);
  const measured = rows.filter((r) => r.bytes !== null)
    .sort((a, b) => (b.bytes ?? 0) - (a.bytes ?? 0))
    .map((r, rank) => ({ ...r, rank }));
  const unmeasured = rows.filter((r) => r.bytes === null);
  const total = measured.reduce((sum, r) => sum + (r.bytes ?? 0), 0);
  return { measured, unmeasured, all: [...measured, ...unmeasured], total, tiles: squarify(measured, aspect) };
}

/** Distinct dark territories; area carries size while colour helps a person track a region. */
const PALETTE: [number, number, number][] = [
  [30, 110, 92], [48, 81, 137], [129, 80, 50], [91, 66, 124],
  [46, 103, 128], [113, 65, 83], [89, 97, 48], [53, 90, 69],
];
const TAIL: [number, number, number] = [58, 70, 65];
const PHOSPHOR: [number, number, number] = [111, 240, 168];
const toneOf = (row: Row): [number, number, number] => (row.tail || row.fromTail ? TAIL : PALETTE[(row.rank ?? 0) % PALETTE.length]);
const css = ([r, g, b]: [number, number, number]) => `rgb(${r} ${g} ${b})`;

const exact = (n: number | null) => (n === null ? 'not measured' : `${n.toLocaleString()} bytes`);
const count = (n: number | null | undefined) => (n === null || n === undefined ? 'not measured' : n.toLocaleString());
const bytesText = (row: Row) => (row.bytes === null ? 'no measure' : `${row.partial ? '≥ ' : ''}${size(row.bytes)}`);

function share(bytes: number | null, total: number): string {
  if (bytes === null || total <= 0) return '—';
  const p = (bytes / total) * 100;
  return bytes > 0 && p < 0.1 ? '<0.1%' : `${p.toFixed(1)}%`;
}

function tally(counts: Record<string, number>, words: Record<string, string>): string {
  const parts = Object.entries(counts).filter(([, n]) => n > 0).map(([k, n]) => `${n.toLocaleString()} ${words[k] ?? k}`);
  return parts.length ? parts.join(' · ') : 'none';
}

function statusText(row: Row): string {
  const g = row.g;
  if (row.tail) return row.bytes === null ? 'folded · no measure' : g.status === 'complete' ? 'folded · measured' : 'folded · partial, lower bound';
  return { complete: 'measured', partial: 'partial · lower bound', skipped: 'skipped · no measure', not_scanned: 'not reached · no measure', not_listed: 'not listed · no measure' }[g.status];
}

function unmeasuredReason(g: SpaceGroup): string {
  if (g.kind === 'other_groups') return 'All folded rows lack a measure; open their exact pages for each reason';
  if (g.status === 'skipped') {
    return {
      mount_point: 'A mount point: its content lives elsewhere and was not followed',
      symlink: 'A symbolic link: it points elsewhere and was not followed',
      cloud: 'A cloud or offline folder: skipped, and it may hold local data',
      other: 'An unrecognized reparse point: skipped, and it may hold local data',
    }[g.skipped ?? 'other'];
  }
  if (g.status === 'not_scanned') return 'The walk stopped at its limit before reaching it';
  if (g.cross_volume) return 'It is on another volume';
  if (g.unreadable_directories.denied) return 'Windows refused to list it';
  if (g.unreadable_directories.vanished) return 'It disappeared during the walk';
  return 'It could not be listed';
}

function whyNotEnterable(row: Row): string {
  if (row.tail) return 'The aggregate has no drill handle. Open its exact rows below to enter a folder.';
  if (row.g.kind === 'top_level_files') return 'These are files directly in this folder; there is no folder to enter.';
  if (row.g.status === 'skipped') return 'The walk does not follow links, mount points or cloud folders, so it issued no handle to enter.';
  return 'This row came without a drill handle.';
}

function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const change = () => setMatches(list.matches);
    change();
    list.addEventListener('change', change);
    return () => list.removeEventListener('change', change);
  }, [query]);
  return matches;
}

function useBoxSize(ref: RefObject<HTMLElement | null>): { w: number; h: number } {
  const [box, setBox] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setBox({ w: entry.contentRect.width, h: entry.contentRect.height }));
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return box;
}

const clsOf = (reading: Reading, name: string): Cls => reading.sections.find((s) => s.name === name)?.class ?? 'derived';

function Atlas({ reading, level, mode, busy, scoped, onLoadMore, hasMore, onSelect, onEnter, onUp }: {
  reading: Reading; level: SpaceLevel; mode: SpaceMode; busy: boolean; scoped: boolean;
  onLoadMore: () => void; hasMore: boolean;
  onSelect: (key: string) => void; onEnter: (row: Row) => void; onUp: (() => void) | null;
}) {
  const wide = useMedia(WIDE_QUERY);
  const groups = useMemo(() => groupsOf(reading), [reading]);
  const model = useMemo(() => buildModel(groups, wide ? WIDE : NARROW), [groups, wide]);
  const pageRows = level.pages.flatMap((page) => groupsOf(page).map((g, i) => ({ ...toRow(g, i), fromTail: true })));
  const selected = model.all.find((r) => r.key === level.selected) ?? pageRows.find((r) => r.key === level.selected) ?? null;
  const drawingSelection = selected?.fromTail ? 'folded' : level.selected;
  const hintId = useId();

  // Keys shared by the tiles and the list: inspect is the default; entering is always a separate act.
  const keys = (row: Row) => (event: KeyboardEvent) => {
    if (event.key === 'Enter' && event.shiftKey && row.enterable) { event.preventDefault(); onEnter(row); }
    else if (event.key === 'Backspace' && onUp) { event.preventDefault(); onUp(); }
  };

  return (
    <>
      <div className={styles.atlas}>
        <div className={styles.drawing}>
          {model.tiles.length === 0 ? (
            <p className={`${styles.map} ${styles.noArea}`}>No measured area at this level. Every row below is either unmeasured or holds zero allocated bytes.</p>
          ) : mode === 'tiles' ? (
            <Tiles tiles={model.tiles} total={model.total} selectedKey={drawingSelection} onSelect={onSelect} keys={keys} onEnter={onEnter} hintId={hintId} />
          ) : (
            <>
              <Planet tiles={model.tiles} total={model.total} selectedKey={drawingSelection} onSelect={onSelect} onEnter={onEnter} onUp={onUp} hintId={hintId} />
              <SurfaceKey tiles={model.tiles} total={model.total} selectedKey={drawingSelection} onSelect={onSelect} />
            </>
          )}
          <p id={hintId} className={`${styles.hint} readout`}>
            {mode === 'planet' ? 'Drag or use the arrow keys to turn the planet; select a region to inspect it. ' : 'Arrow keys move between tiles; selecting inspects. '}
            Enter folder walks the selection on its own; double-click or Shift+Enter dives in.{onUp ? ' Backspace returns to the parent walk.' : ''}
          </p>
        </div>
        <Inspector row={selected} total={model.total} busy={busy} walkedAt={reading.asked_at} onEnter={onEnter} />
      </div>

      <SectionHead title="Measured rows" cls={clsOf(reading, 'groups')} basis={basisOf(reading, 'groups')}
        note="Exact shares of what this walk measured, near side and far side alike. A partial row is a lower bound, marked ≥." />
      <ol className={styles.ranked}>
        {model.measured.map((row) => (
          <li key={row.key}>
            <RowButton row={row} total={model.total} on={row.key === level.selected} onSelect={onSelect} onEnter={onEnter} keys={keys} />
          </li>
        ))}
      </ol>

      {(pageRows.length > 0 || hasMore || level.pageProblem) ? (
        <div className={styles.pages}>
          <SectionHead title="Inside folded territory" cls="derived"
            note="These exact rows came from the same walk as the aggregate grey territory. Load another page to reveal more names and child handles; their areas remain inside the folded region above." />
          {pageRows.length > 0 ? <ol className={styles.ranked}>
            {pageRows.map((row) => <li key={row.key}>
              <RowButton row={row} total={model.total} on={row.key === level.selected} onSelect={onSelect} onEnter={onEnter} keys={keys} />
            </li>)}
          </ol> : null}
          {hasMore ? <button className={styles.loadMore} onClick={onLoadMore} disabled={level.pageAsking}>
            {level.pageAsking ? 'Loading held rows…' : 'Show more folders from this walk'}
          </button> : null}
          {level.pageProblem ? <p className={styles.pageProblem} role="alert">{level.pageProblem}</p> : null}
        </div>
      ) : null}

      {model.unmeasured.length ? (
        <>
          <SectionHead title="Not measured · no area" cls={clsOf(reading, 'groups')}
            note="These rows were not listed by this walk, so they have no size and no place in either drawing." />
          <ol className={styles.ranked}>
            {model.unmeasured.map((row) => (
              <li key={row.key}>
                <RowButton row={row} total={model.total} on={row.key === level.selected} onSelect={onSelect} onEnter={onEnter} keys={keys} />
              </li>
            ))}
          </ol>
        </>
      ) : null}

      <Ledger reading={reading} scoped={scoped} />
      <CoverageSection reading={reading} />
    </>
  );
}

function RowButton({ row, total, on, onSelect, onEnter, keys }: {
  row: Row; total: number; on: boolean; onSelect: (key: string) => void; onEnter: (row: Row) => void; keys: (row: Row) => (event: KeyboardEvent) => void;
}) {
  const g = row.g;
  const ratio = row.bytes !== null && total > 0 ? row.bytes / total : 0;
  return (
    <button className={`${styles.rankRow} ${on ? styles.rankOn : ''}`} aria-pressed={on}
      onClick={() => onSelect(row.key)} onDoubleClick={() => { if (row.enterable) onEnter(row); }} onKeyDown={keys(row)}>
      <span className={`${styles.swatch} ${row.bytes === null ? styles.swatchNone : ''} ${row.partial ? styles.swatchPartial : ''}`}
        style={row.bytes !== null ? ({ '--tile': css(toneOf(row)) } as CSSProperties) : undefined} aria-hidden="true" />
      <span className={`${styles.rank} readout`}>{row.rank !== null ? row.rank + 1 : '·'}</span>
      <span className={styles.rowName}>
        {row.name}
        {g.label && g.label !== g.name ? <span className={`${styles.rowLabel} readout`}> · {g.label}</span> : null}
      </span>
      <span className={styles.shareBar} aria-hidden="true"><span style={{ width: `${ratio * 100}%` }} /></span>
      <span className={`${styles.rowBytes} readout`}>{bytesText(row)}</span>
      <span className={`${styles.rowShare} readout`}>{share(row.bytes, total)}</span>
      <span className={`${styles.rowState} readout`}>
        {row.bytes === null ? unmeasuredReason(g).toLowerCase() : `${row.partial ? 'partial' : 'measured'}${row.bytes === 0 ? ' · no area' : ''}`}
        {row.enterable ? ' · can enter' : row.tail ? ' · folded' : ''}
      </span>
    </button>
  );
}

/** Arrow keys walk the tiles in rank order, the order the list reads. */
const TILE_STEPS: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

function Tiles({ tiles, total, selectedKey, onSelect, keys, onEnter, hintId }: {
  tiles: Tile[]; total: number; selectedKey: string | null; onSelect: (key: string) => void;
  keys: (row: Row) => (event: KeyboardEvent) => void; onEnter: (row: Row) => void; hintId: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const px = useBoxSize(box);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const roving = tiles.some((t) => t.row.key === selectedKey) ? selectedKey : tiles[0].row.key;
  const move = (to: number) => {
    const next = tiles[(to + tiles.length) % tiles.length];
    onSelect(next.row.key);
    buttons.current.get(next.row.key)?.focus();
  };

  return (
    <div ref={box} className={styles.map} role="group" aria-label="Tiles: area is allocated bytes among measured rows" aria-describedby={hintId}>
      {tiles.map((t, index) => {
        const { row } = t;
        const on = row.key === selectedKey;
        const tw = t.w * px.w, th = t.h * px.h;
        const style = { left: `${t.x * 100}%`, top: `${t.y * 100}%`, width: `${t.w * 100}%`, height: `${t.h * 100}%`, '--tile': css(toneOf(row)) } as CSSProperties;
        return (
          <button
            key={row.key}
            ref={(element) => { if (element) buttons.current.set(row.key, element); else buttons.current.delete(row.key); }}
            className={`${styles.tile} ${row.partial ? styles.tilePartial : ''} ${row.tail ? styles.tileTail : ''} ${on ? styles.tileOn : ''}`}
            style={style}
            tabIndex={row.key === roving ? 0 : -1}
            aria-pressed={on}
            aria-label={`${row.name}, ${bytesText(row)}${row.partial ? ' lower bound' : ''}, ${share(row.bytes, total)} of measured${row.enterable ? ', can enter' : ''}`}
            onClick={() => onSelect(row.key)}
            onDoubleClick={() => { if (row.enterable) onEnter(row); }}
            onKeyDown={(event) => {
              const step = TILE_STEPS[event.key];
              if (step) { event.preventDefault(); move(index + step); }
              else if (event.key === 'Home') { event.preventDefault(); move(0); }
              else if (event.key === 'End') { event.preventDefault(); move(tiles.length - 1); }
              else keys(row)(event);
            }}
          >
            {tw >= 64 && th >= 30 ? <span className={styles.tileName}>{row.name}</span> : null}
            {tw >= 84 && th >= 50 ? <span className={styles.tileFigure}>{bytesText(row)} · {share(row.bytes, total)}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The planet: the same rectangles on an equal-area sphere
// ---------------------------------------------------------------------------

const LUT_U = 512;
const LUT_V = 256;
const BLANK: [number, number, number] = [11, 26, 22];
const DEFAULT_VIEW = { yaw: 0.55, pitch: -0.18 };
const clampPitch = (pitch: number) => Math.max(-1.2, Math.min(1.2, pitch));
const wrapAngle = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));

/**
 * Which rectangle covers each cell of the (longitude, sin latitude) plane. u is longitude over the
 * full turn and v is sin(latitude) mapped to 0..1; the sphere's area element is constant in these
 * two coordinates, so a rectangle's share of the plane is its share of the surface.
 */
function buildLut(tiles: Tile[]) {
  const index = new Int16Array(LUT_U * LUT_V).fill(-1);
  tiles.forEach((t, n) => {
    const i0 = Math.round(t.x * LUT_U), j0 = Math.round(t.y * LUT_V);
    const i1 = Math.min(LUT_U, Math.max(i0 + 1, Math.round((t.x + t.w) * LUT_U)));
    const j1 = Math.min(LUT_V, Math.max(j0 + 1, Math.round((t.y + t.h) * LUT_V)));
    for (let j = j0; j < j1; j += 1) for (let i = i0; i < i1; i += 1) index[j * LUT_U + i] = n;
  });
  const edge = new Uint8Array(LUT_U * LUT_V);
  for (let j = 0; j < LUT_V; j += 1) {
    for (let i = 0; i < LUT_U; i += 1) {
      const here = index[j * LUT_U + i];
      const right = index[j * LUT_U + ((i + 1) % LUT_U)];
      const below = j + 1 < LUT_V ? index[(j + 1) * LUT_U + i] : here;
      if (here !== right || here !== below) edge[j * LUT_U + i] = 1;
    }
  }
  return { index, edge };
}

/** A point of the unit plane on the sphere, before rotation. y grows downward, as the screen does. */
function onSphere(u: number, v: number) {
  const lon = u * Math.PI * 2 - Math.PI;
  const y = v * 2 - 1;
  const ring = Math.sqrt(Math.max(0, 1 - y * y));
  return { x: Math.cos(lon) * ring, y, z: Math.sin(lon) * ring };
}

function rotate(p: { x: number; y: number; z: number }, yaw: number, pitch: number) {
  const X = p.x * Math.cos(yaw) - p.z * Math.sin(yaw);
  const z1 = p.x * Math.sin(yaw) + p.z * Math.cos(yaw);
  return { X, Y: p.y * Math.cos(pitch) - z1 * Math.sin(pitch), Z: p.y * Math.sin(pitch) + z1 * Math.cos(pitch) };
}

/** The view that puts a rectangle's centre in front of the reader. */
function facingTile(t: Tile) {
  const p = onSphere(t.x + t.w / 2, t.y + t.h / 2);
  return { yaw: Math.atan2(p.x, p.z), pitch: clampPitch(Math.atan2(p.y, Math.hypot(p.x, p.z))) };
}

function nearSide(tiles: Tile[], view: { yaw: number; pitch: number }): string[] {
  return tiles
    .map((t) => ({ t, Z: rotate(onSphere(t.x + t.w / 2, t.y + t.h / 2), view.yaw, view.pitch).Z }))
    .filter(({ Z }) => Z > 0.2)
    .sort((a, b) => b.t.w * b.t.h - a.t.w * a.t.h)
    .slice(0, 4)
    .map(({ t }) => t.row.name);
}

function Planet({ tiles, total, selectedKey, onSelect, onEnter, onUp, hintId }: {
  tiles: Tile[]; total: number; selectedKey: string | null; onSelect: (key: string) => void;
  onEnter: (row: Row) => void; onUp: (() => void) | null; hintId: string;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const surface = useRef<HTMLCanvasElement | null>(null);
  const view = useRef({ ...DEFAULT_VIEW });
  const geometry = useRef({ cx: 0, cy: 0, r: 1 });
  const frame = useRef(0);
  const motion = useRef(0);
  const drag = useRef<{ startX: number; startY: number; lastX: number; lastY: number; moved: boolean } | null>(null);
  const pickedHere = useRef(false);
  const first = useRef(true);
  const lut = useMemo(() => buildLut(tiles), [tiles]);
  const input = useRef({ tiles, lut, selectedKey, total });
  const [settled, setSettled] = useState({ ...DEFAULT_VIEW });

  // Drawn on demand only: a change of layout, selection, size, a drag or a turn. Idle, nothing runs.
  const draw = useCallback(() => {
    const element = canvas.current;
    const ctx = element?.getContext('2d');
    if (!element || !ctx) return;
    const w = element.clientWidth, h = element.clientHeight;
    if (!w || !h) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (element.width !== Math.round(w * dpr) || element.height !== Math.round(h * dpr)) {
      element.width = Math.round(w * dpr);
      element.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const r = Math.min(w, h) * 0.43, cx = w / 2, cy = h / 2;
    geometry.current = { cx, cy, r };
    const { tiles: all, lut: map, selectedKey: chosen, total: sum } = input.current;
    const { yaw, pitch } = view.current;
    const selectedIndex = all.findIndex((t) => t.row.key === chosen);
    const colors = all.map((t, n) => {
      const base = toneOf(t.row);
      return n === selectedIndex ? base.map((c, k) => c * 0.55 + PHOSPHOR[k] * 0.45) as [number, number, number] : base;
    });

    // The lit surface, pixel by pixel through the inverse rotation.
    const D = Math.max(160, Math.min(400, Math.round(2 * r * dpr)));
    const off = surface.current ?? (surface.current = document.createElement('canvas'));
    if (off.width !== D) { off.width = D; off.height = D; }
    const octx = off.getContext('2d');
    if (!octx) return;
    const image = octx.createImageData(D, D);
    const data = image.data;
    const cY = Math.cos(yaw), sY = Math.sin(yaw), cX = Math.cos(pitch), sX = Math.sin(pitch);
    for (let j = 0; j < D; j += 1) {
      const Y = ((j + 0.5) / D) * 2 - 1;
      for (let i = 0; i < D; i += 1) {
        const X = ((i + 0.5) / D) * 2 - 1;
        const d2 = X * X + Y * Y;
        if (d2 >= 1) continue;
        const Z = Math.sqrt(1 - d2);
        const y0 = Y * cX + Z * sX, z1 = -Y * sX + Z * cX;
        const x0 = X * cY + z1 * sY, z0 = -X * sY + z1 * cY;
        const u = (Math.atan2(z0, x0) + Math.PI) / (Math.PI * 2);
        const v = (y0 + 1) / 2;
        const cell = Math.min(LUT_V - 1, Math.max(0, (v * LUT_V) | 0)) * LUT_U + Math.min(LUT_U - 1, (u * LUT_U) | 0);
        const n = map.index[cell];
        const rgb = n >= 0 ? colors[n] : BLANK;
        let shade = 0.52 + 0.4 * Z + 0.14 * Math.max(0, -X * 0.6 - Y * 0.8);
        if (map.edge[cell]) shade *= 0.5;
        if (n >= 0 && all[n].row.partial && (i + j) % 7 < 2) shade *= 0.66;
        const o = (j * D + i) * 4;
        data[o] = Math.min(255, rgb[0] * shade + 4);
        data[o + 1] = Math.min(255, rgb[1] * shade + 4);
        data[o + 2] = Math.min(255, rgb[2] * shade + 4);
        data[o + 3] = 255;
      }
    }
    octx.putImageData(image, 0, 0);
    ctx.drawImage(off, cx - r, cy - r, r * 2, r * 2);

    // An equal-area graticule: parallels that split the surface into quarters, meridians every 30°.
    const line = (points: { x: number; y: number; z: number }[]) => {
      ctx.beginPath();
      let pen = false;
      for (const p of points) {
        const q = rotate(p, yaw, pitch);
        if (q.Z <= 0) { pen = false; continue; }
        if (pen) ctx.lineTo(cx + q.X * r, cy + q.Y * r); else ctx.moveTo(cx + q.X * r, cy + q.Y * r);
        pen = true;
      }
      ctx.stroke();
    };
    ctx.strokeStyle = 'rgba(201, 255, 225, 0.13)';
    ctx.lineWidth = 0.8;
    for (const v of [0.25, 0.5, 0.75]) line(Array.from({ length: 121 }, (_, k) => onSphere(k / 120, v)));
    for (let m = 0; m < 12; m += 1) line(Array.from({ length: 61 }, (_, k) => onSphere(m / 12, 0.01 + (k / 60) * 0.98)));

    // The rim: the one glow on the page.
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(111, 240, 168, 0.55)';
    ctx.lineWidth = 1.2;
    ctx.shadowColor = 'rgba(111, 240, 168, 0.6)';
    ctx.shadowBlur = 14;
    ctx.stroke();
    ctx.restore();

    // Labels where they fit: the selection always when it faces the reader, the largest few on a wide stage.
    ctx.font = `${w < 420 ? 11 : 12}px 'JetBrains Mono', Menlo, Consolas, monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const labelled = all
      .map((t, n) => ({ t, n, q: rotate(onSphere(t.x + t.w / 2, t.y + t.h / 2), yaw, pitch) }))
      .filter(({ q, t, n }) => q.Z > 0.25 && (n === selectedIndex || (w >= 420 && t.w * t.h >= 0.03)))
      .sort((a, b) => Number(b.n === selectedIndex) - Number(a.n === selectedIndex) || b.t.w * b.t.h - a.t.w * a.t.h)
      .slice(0, w >= 420 ? 5 : 1);
    for (const { t, n, q } of labelled) {
      const name = t.row.name.length > 24 ? `${t.row.name.slice(0, 23)}…` : t.row.name;
      const text = `${name} · ${share(t.row.bytes, sum)}`;
      const x = cx + q.X * r, y = cy + q.Y * r, width = ctx.measureText(text).width;
      ctx.fillStyle = 'rgba(6, 16, 13, 0.84)';
      ctx.fillRect(x - width / 2 - 6, y - 10, width + 12, 20);
      ctx.fillStyle = n === selectedIndex ? '#c9ffe1' : '#eaf3ee';
      ctx.fillText(text, x, y);
    }
  }, []);

  const schedule = useCallback(() => {
    if (!frame.current) frame.current = requestAnimationFrame(() => { frame.current = 0; draw(); });
  }, [draw]);
  const settle = useCallback(() => setSettled({ ...view.current }), []);

  const turnTo = useCallback((target: { yaw: number; pitch: number }) => {
    cancelAnimationFrame(motion.current);
    const from = { ...view.current };
    const to = { yaw: from.yaw + wrapAngle(target.yaw - from.yaw), pitch: clampPitch(target.pitch) };
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { view.current = to; draw(); settle(); return; }
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / 360);
      const e = 1 - (1 - t) ** 3;
      view.current = { yaw: from.yaw + (to.yaw - from.yaw) * e, pitch: from.pitch + (to.pitch - from.pitch) * e };
      draw();
      if (t < 1) motion.current = requestAnimationFrame(step);
      else { motion.current = 0; settle(); }
    };
    motion.current = requestAnimationFrame(step);
  }, [draw, settle]);

  useLayoutEffect(() => {
    input.current = { tiles, lut, selectedKey, total };
    draw();
  }, [tiles, lut, selectedKey, total, draw]);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const observer = new ResizeObserver(() => schedule());
    observer.observe(element);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame.current);
      cancelAnimationFrame(motion.current);
      frame.current = 0;
      motion.current = 0;
    };
  }, [schedule]);

  // A selection made in the list, the key or the tiles turns its region to face the reader.
  // One picked on the planet is already where the pointer is.
  useEffect(() => {
    const target = tiles.find((t) => t.row.key === selectedKey);
    const opening = first.current;
    first.current = false;
    if (pickedHere.current) { pickedHere.current = false; return; }
    if (!target) return;
    if (opening) { view.current = facingTile(target); draw(); settle(); return; }
    turnTo(facingTile(target));
  }, [selectedKey, tiles, turnTo, draw, settle]);

  const pick = (clientX: number, clientY: number): string | null => {
    const element = canvas.current;
    if (!element) return null;
    const box = element.getBoundingClientRect();
    const { cx, cy, r } = geometry.current;
    const X = (clientX - box.left - cx) / r, Y = (clientY - box.top - cy) / r;
    const d2 = X * X + Y * Y;
    if (d2 > 1) return null;
    const Z = Math.sqrt(1 - d2);
    const { yaw, pitch } = view.current;
    const y0 = Y * Math.cos(pitch) + Z * Math.sin(pitch), z1 = -Y * Math.sin(pitch) + Z * Math.cos(pitch);
    const x0 = X * Math.cos(yaw) + z1 * Math.sin(yaw), z0 = -X * Math.sin(yaw) + z1 * Math.cos(yaw);
    const u = (Math.atan2(z0, x0) + Math.PI) / (Math.PI * 2), v = (y0 + 1) / 2;
    const n = lut.index[Math.min(LUT_V - 1, Math.max(0, (v * LUT_V) | 0)) * LUT_U + Math.min(LUT_U - 1, (u * LUT_U) | 0)];
    return n >= 0 ? tiles[n].row.key : null;
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    cancelAnimationFrame(motion.current);
    drag.current = { startX: event.clientX, startY: event.clientY, lastX: event.clientX, lastY: event.clientY, moved: false };
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    if (!d.moved && Math.hypot(event.clientX - d.startX, event.clientY - d.startY) < 4) return;
    d.moved = true;
    view.current = { yaw: view.current.yaw - (event.clientX - d.lastX) * 0.009, pitch: clampPitch(view.current.pitch - (event.clientY - d.lastY) * 0.009) };
    d.lastX = event.clientX;
    d.lastY = event.clientY;
    schedule();
  };
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.moved) { settle(); return; }
    const key = pick(event.clientX, event.clientY);
    if (key && key !== selectedKey) { pickedHere.current = true; onSelect(key); }
  };
  const onPointerCancel = () => { if (drag.current?.moved) settle(); drag.current = null; };

  const nudge = (yaw: number, pitch: number) => turnTo({ yaw: view.current.yaw + yaw, pitch: view.current.pitch + pitch });
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, [number, number]> = { ArrowLeft: [0.3, 0], ArrowRight: [-0.3, 0], ArrowUp: [0, 0.25], ArrowDown: [0, -0.25] };
    const m = moves[event.key];
    if (m) { event.preventDefault(); nudge(m[0], m[1]); }
    else if (event.key === 'Home') { event.preventDefault(); turnTo(DEFAULT_VIEW); }
    else if (event.key === 'Enter' && event.shiftKey) {
      const target = tiles.find((t) => t.row.key === selectedKey)?.row;
      if (target?.enterable) { event.preventDefault(); onEnter(target); }
    }
    else if (event.key === 'Backspace' && onUp) { event.preventDefault(); onUp(); }
  };

  const facingDegrees = Math.round((((Math.PI / 2 - settled.yaw) * 180) / Math.PI % 360 + 360) % 360);
  const tilt = Math.round((settled.pitch * 180) / Math.PI);
  const near = nearSide(tiles, settled);
  const chosen = tiles.find((t) => t.row.key === selectedKey) ?? null;

  return (
    <div>
      <div
        className={`${styles.map} ${styles.planetStage}`}
        role="slider"
        tabIndex={0}
        aria-label="Planet: turn to see each region; area is allocated bytes among measured rows"
        aria-describedby={hintId}
        aria-valuemin={0}
        aria-valuemax={359}
        aria-valuenow={facingDegrees}
        aria-valuetext={`Facing ${facingDegrees}°, tilted ${Math.abs(tilt)}° ${tilt < 0 ? 'from above' : 'from below'}. Near side: ${near.join(', ') || 'no labelled region'}.`}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        onDoubleClick={(event) => {
          const key = pick(event.clientX, event.clientY);
          const target = tiles.find((t) => t.row.key === key)?.row;
          if (target?.enterable) onEnter(target);
        }}
      >
        <canvas ref={canvas} className={styles.planet} aria-hidden="true" />
      </div>
      <div className={styles.planetControls}>
        <button className={styles.control} onClick={() => nudge(0.5, 0)} aria-label="Turn the planet left">← Turn</button>
        <button className={styles.control} onClick={() => nudge(-0.5, 0)} aria-label="Turn the planet right">Turn →</button>
        <button className={styles.control} onClick={() => nudge(0, 0.3)} aria-label="Tilt the planet up">Tilt ↑</button>
        <button className={styles.control} onClick={() => nudge(0, -0.3)} aria-label="Tilt the planet down">Tilt ↓</button>
        <button className={styles.control} onClick={() => { if (chosen) turnTo(facingTile(chosen)); }} disabled={!chosen}>Face selection</button>
      </div>
    </div>
  );
}

/**
 * The whole surface at once, near side and far side, in rank order. A pointer shortcut only: the
 * ranked list below is the accessible equivalent and carries the exact figures.
 */
function SurfaceKey({ tiles, total, selectedKey, onSelect }: { tiles: Tile[]; total: number; selectedKey: string | null; onSelect: (key: string) => void }) {
  return (
    <div className={styles.surfaceKey}>
      <p className={`${styles.surfaceLabel} label`}>Whole surface · near and far side</p>
      <div className={styles.surface} aria-hidden="true">
        {tiles.map((t) => (
          <button key={t.row.key} type="button" tabIndex={-1}
            className={`${styles.surfacePart} ${t.row.partial ? styles.tilePartial : ''} ${t.row.key === selectedKey ? styles.surfaceOn : ''}`}
            style={{ flexGrow: (t.row.bytes ?? 0) / total, '--tile': css(toneOf(t.row)) } as CSSProperties}
            title={`${t.row.name} · ${bytesText(t.row)} · ${share(t.row.bytes, total)}`}
            onClick={() => onSelect(t.row.key)} />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inspecting a row, which walks nothing
// ---------------------------------------------------------------------------

const AGE_LAYERS = [
  ['last_7_days', 'Last 7 days'], ['days_7_to_30', '7–30 days'],
  ['days_30_to_180', '30–180 days'], ['days_180_to_365', '180–365 days'],
  ['older_than_365_days', 'Over a year'], ['unknown', 'Unknown'],
] as const;

/** A core sample of observed bytes by last write. It makes no claim about last access. */
function CoreSample({ row }: { row: Row }) {
  const g = row.g;
  const ages = g.last_write_age_allocated_bytes;
  if (!ages || g.allocated_bytes === null || g.allocated_bytes <= 0) return null;
  const total = g.allocated_bytes;
  return (
    <div className={styles.coreSample}>
      <h3 className={`${styles.coreTitle} label`}>Core sample · last write</h3>
      <p className={styles.coreNote}>The measured allocation in this row, sorted by when Windows says its files were last written. This is not a measure of last use.{row.partial ? ' More material may be unmeasured.' : ''}</p>
      <div className={styles.coreLayers} role="img" aria-label={AGE_LAYERS.map(([key, label]) => `${label}: ${exact(ages[key] ?? 0)}`).join('; ')}>
        {AGE_LAYERS.filter(([key]) => (ages[key] ?? 0) > 0).map(([key, label], index) => (
          <span key={key} className={styles.coreLayer} style={{ width: `${((ages[key] ?? 0) / total) * 100}%`, background: css(PALETTE[index % PALETTE.length]) }} title={`${label}: ${exact(ages[key] ?? 0)}`} />
        ))}
      </div>
      <ol className={styles.coreLegend}>
        {AGE_LAYERS.filter(([key]) => (ages[key] ?? 0) > 0).map(([key, label], index) => (
          <li key={key}><i style={{ background: css(PALETTE[index % PALETTE.length]) }} aria-hidden="true" /><span>{label}</span><strong>{size(ages[key] ?? 0)}</strong></li>
        ))}
      </ol>
    </div>
  );
}

/** Matter lens: compare the on-disk allocation with file lengths without implying savings. */
function Matter({ row }: { row: Row }) {
  const allocated = row.g.allocated_bytes, logical = row.g.logical_bytes;
  if (allocated === null || logical === null || (!allocated && !logical)) return null;
  const scale = Math.max(allocated, logical);
  return (
    <div className={styles.matter}>
      <h3 className={`${styles.coreTitle} label`}>Matter · local vs logical</h3>
      <p className={styles.coreNote}>File length and allocated bytes can differ because of compression, sparse files, placeholders or cluster rounding. Their difference is not a deletion estimate.</p>
      <div className={styles.matterRow}><span>Allocated</span><b><i style={{ width: `${allocated / scale * 100}%` }} /></b><strong>{size(allocated)}</strong></div>
      <div className={styles.matterRow}><span>Logical</span><b><i style={{ width: `${logical / scale * 100}%` }} /></b><strong>{size(logical)}</strong></div>
    </div>
  );
}

function Inspector({ row, total, busy, walkedAt, onEnter }: {
  row: Row | null; total: number; busy: boolean; walkedAt: string; onEnter: (row: Row) => void;
}) {
  if (!row) {
    return (
      <aside className={styles.inspector} aria-label="Selected row">
        <p className={styles.inspectEmpty}>Select a tile, a region or a row to inspect it here. Inspecting walks nothing; Enter folder starts a separate walk of that folder.</p>
      </aside>
    );
  }
  const g = row.g;
  const facts: [string, ReactNode][] = [
    ['Allocated', exact(g.allocated_bytes)],
    ['Logical length', exact(g.logical_bytes)],
    ['Files', count(g.files)],
    ['Folders inside', count(g.directories)],
    ['Hard-link repeats', g.link_repeats === null ? 'not measured' : `${g.link_repeats.toLocaleString()} · counted once`],
    ['Cloud placeholders', g.placeholder_files === null ? 'not measured' : `${g.placeholder_files.toLocaleString()} files · ${exact(g.placeholder_logical_bytes)} logical`],
    ['Compressed or sparse', count(g.compressed_or_sparse_files)],
    ['Reparse-point files', count(g.reparse_files)],
    ['Folders listed', g.visited_directories.toLocaleString()],
    ['Folders not reached', g.unvisited_directories.toLocaleString()],
    ['Skipped folders', tally(g.skipped_directories, SKIP_WORDS)],
    ['Could not list', tally(g.unreadable_directories, UNREADABLE_WORDS)],
    ['On another volume', g.cross_volume.toLocaleString()],
  ];
  if (row.tail) facts.unshift(['Rows folded', count(g.groups)]);
  return (
    <aside className={styles.inspector} aria-label={`Selected: ${row.name}`}>
      <p className="label">{statusText(row)}</p>
      <h2 className={`${styles.inspectName} display`}>{row.name}</h2>
      {g.label && g.label !== g.name ? <p className={`${styles.inspectLabel} readout`}>Windows known folder: {g.label}</p> : null}
      <p className={`${styles.inspectFigure} readout`}>
        {bytesText(row)}{row.bytes !== null ? <> · {share(row.bytes, total)} of this walk&rsquo;s measured allocation</> : null}
      </p>
      {row.fromTail ? <p className={styles.coreNote}>This folder is represented by the aggregate grey territory in the map.</p> : null}
      <Matter row={row} />
      <CoreSample row={row} />
      {row.bytes === null ? <p className={styles.inspectWhy}>{unmeasuredReason(g)}. It has no size and no area.</p> : null}
      {row.enterable ? (
        <div className={styles.enterBlock}>
          <button className={styles.enter} onClick={() => onEnter(row)} disabled={busy}>Enter folder</button>
          <p className={styles.enterNote}>Starts a separate walk of this folder, up to 30 seconds. Its handle came from the walk taken {clock.format(new Date(walkedAt))} and lasts 30 minutes.</p>
        </div>
      ) : <p className={styles.inspectWhy}>{whyNotEnterable(row)}</p>}
      <Facts rows={facts} />
      <details className={styles.returned}>
        <summary className="readout">Row as returned</summary>
        <Tree value={g} />
      </details>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// The ledger and the walk's reach
// ---------------------------------------------------------------------------

function Ledger({ reading, scoped }: { reading: Reading; scoped: boolean }) {
  const rec = part<Reconciliation>(reading, 'reconciliation');
  const volume = part<Collection>(reading, 'collection')?.volume ?? null;
  if (!rec) return null;
  const attributed = `${rec.scan_lower_bound ? '≥ ' : ''}${size(rec.scan_allocated_bytes)}`;
  const restLabel = scoped ? 'Outside this selected walk or unmeasured' : 'Volume used, not attributed to this walk';
  const rest = rec.not_attributed_bytes;
  const rows: [string, ReactNode][] = [];
  if (volume) {
    rows.push(['Volume', `${volume.name} · ${volume.file_system}`]);
    rows.push(['Capacity', `${size(volume.total_bytes)} · ${volume.total_bytes.toLocaleString()} bytes`]);
    rows.push(['Free', `${size(volume.free_bytes)} · ${size(volume.available_bytes)} available to this user`]);
  }
  rows.push(['Used', rec.volume_used_bytes === null ? 'unknown' : `${size(rec.volume_used_bytes)} · total minus free`]);
  rows.push([scoped ? 'Attributed by this walk' : 'Attributed to Home', `${attributed} · ${rec.scan_allocated_bytes.toLocaleString()} bytes${rec.scan_lower_bound ? ', a lower bound' : ''}`]);
  rows.push([restLabel, rest === null ? 'unknown' : `${size(rest)} · ${rest.toLocaleString()} bytes`]);

  const capacity = volume?.total_bytes ?? 0;
  const used = rec.volume_used_bytes ?? 0;
  const width = (n: number) => `${capacity > 0 ? Math.max(0, Math.min(100, (n / capacity) * 100)) : 0}%`;

  return (
    <div className={styles.ledger}>
      <SectionHead title="Volume ledger" cls={clsOf(reading, 'reconciliation')} basis={basisOf(reading, 'reconciliation')}
        note={scoped
          ? 'The remainder is everything on this volume outside the selected folder, plus whatever this walk skipped or could not list. It is not the Home remainder, not a folder and not reclaimable space.'
          : 'The remainder lies outside Home, in parts of Home this walk skipped or could not list, or in file-system metadata. It is not a folder and not reclaimable space.'} />
      {capacity > 0 && rest !== null ? (
        <>
          <div className={styles.ledgerBar} role="img"
            aria-label={`Of ${size(capacity)} capacity: ${attributed} attributed by this walk, ${rest === null ? 'an unknown amount' : size(rest)} ${restLabel.toLowerCase()}, ${size(volume?.free_bytes ?? 0)} free.`}>
            <span className={`${styles.ledgerWalk} ${rec.scan_lower_bound ? styles.ledgerLower : ''}`} style={{ width: width(Math.min(rec.scan_allocated_bytes, used)) }} />
            {rest !== null ? <span className={styles.ledgerRest} style={{ width: width(rest) }} /> : null}
          </div>
          <p className={`${styles.ledgerKey} readout`}>
            <span><i className={styles.ledgerWalk} aria-hidden="true" /> attributed</span>
            <span><i className={styles.ledgerRest} aria-hidden="true" /> {rest === null ? 'remainder unknown' : 'remainder'}</span>
            <span><i className={styles.ledgerFree} aria-hidden="true" /> free</span>
          </p>
        </>
      ) : capacity > 0 ? <p className={styles.coreNote}>The scan and volume figures do not reconcile, so no proportional ledger bar is drawn.</p> : null}
      <Facts rows={rows} />
    </div>
  );
}

function CoverageSection({ reading }: { reading: Reading }) {
  const c = part<Coverage>(reading, 'coverage');
  const collection = part<Collection>(reading, 'collection');
  const examples = part<Example[]>(reading, 'examples');
  if (!c) return null;
  const limits = collection?.limits;
  const walk = c.stopped === 'time_limit' ? `stopped at its ${limits?.seconds ?? '?'} s limit; totals are lower bounds`
    : c.stopped === 'entry_limit' ? `stopped at its ${limits?.max_entries.toLocaleString() ?? '?'}-entry limit; totals are lower bounds`
    : c.complete ? 'finished; every folder that might hold local data was listed' : 'finished, with the gaps below; totals are lower bounds';
  const skipped = c.skipped_directories;
  const rows: [string, ReactNode][] = [
    ['Walk', walk],
    ['Entries read', `${count(collection?.entries)}${limits ? ` of up to ${limits.max_entries.toLocaleString()}` : ''}`],
    ['Walk time', collection?.elapsed_ms != null ? `${(collection.elapsed_ms / 1000).toFixed(1)} s` : 'not reported'],
    ['Folders not reached', c.unvisited_directories.toLocaleString()],
    ['Not followed, content elsewhere', `${(skipped.mount_point ?? 0).toLocaleString()} mount points · ${(skipped.symlink ?? 0).toLocaleString()} symbolic links`],
    ['Skipped, may hold data', `${(skipped.cloud ?? 0).toLocaleString()} cloud · ${(skipped.other ?? 0).toLocaleString()} unrecognized`],
    ['Could not be listed', tally(c.unreadable_directories, UNREADABLE_WORDS)],
    ['On another volume', c.cross_volume_directories.toLocaleString()],
    ['Hard-link repeats', `${c.link_repeats.toLocaleString()} · each counted once within this walk`],
    ['Files without an ID', c.unidentified_files.toLocaleString()],
    ['Cloud placeholders exposed', collection ? (collection.placeholders_exposed ? 'yes' : 'no; placeholder counts may be low') : 'not reported'],
  ];
  return (
    <div className={styles.coverage}>
      <SectionHead title="Coverage" cls={clsOf(reading, 'coverage')} basis={basisOf(reading, 'coverage')} />
      <Facts rows={rows} />
      {examples?.length ? (
        <details className={styles.returned}>
          <summary className="readout">Skipped or unreadable examples · relative to this folder</summary>
          <ul className={styles.examples}>
            {examples.map((e, i) => <li key={i} className="readout"><span className={styles.exampleReason}>{e.reason.replace(/_/g, ' ')}</span> {e.path}</li>)}
          </ul>
        </details>
      ) : null}
      {collection ? (
        <details className={styles.returned}>
          <summary className="readout">Collection as returned · {clsOf(reading, 'collection')}</summary>
          <Tree value={collection} />
        </details>
      ) : null}
    </div>
  );
}
