import { create } from 'zustand';
import type { Reading } from './api';

export type ViewId = 'home' | 'case' | 'record' | 'errors' | 'crashes' | 'machine' | 'performance' | 'space' | 'diagnostics' | 'signals' | 'stack' | 'agents';

/**
 * Direction C's rail: home and the case sit above the readings, which become lenses a case adds
 * evidence through. The studio page copies these names; change them there too.
 */
export type ViewGroup = 'Cases' | 'Readings' | 'Agent';

interface PerformanceViewState {
  hours: number;
  endChoice: 'now' | 'held';
  /** An exact numeric sample timestamp, never a process name or retained row. */
  selectedAt: string | null;
}

interface CrashesViewState {
  stopCount: number;
  faultCount: number;
  faultKind: string | null;
  stopId: string | null;
  faultId: string | null;
  dumpId: string | null;
  reliabilityDay: string | null;
  /** The one stop whose on-demand change history is open, with its exact estimated boundary. */
  changesStopId: string | null;
  changesBefore: string | null;
  /** Which open evidence to return to after following its System record. */
  focus: 'stop' | 'fault' | 'dump' | null;
}

const INITIAL_CRASHES_VIEW: CrashesViewState = { stopCount: 5, faultCount: 30, faultKind: null,
  stopId: null, faultId: null, dumpId: null, reliabilityDay: null,
  changesStopId: null, changesBefore: null, focus: null };

export type SpaceMode = 'tiles' | 'planet';

/**
 * One level of the Space walk path. Each level is its own walk of its own scope: a held level is
 * the answer that walk gave, never a slice of its parent's. The scope handle is opaque and
 * short-lived; it stays in this tab and never enters the address.
 */
export interface SpaceLevel {
  /** '' is Home; otherwise the handle a parent walk issued on this folder's row. */
  scopeId: string;
  /** The row this level was entered from, as the parent walk named it. Null for Home. */
  name: string | null;
  label: string | null;
  /** What the parent walk's row said, kept only to set beside this walk, never to subtract. */
  entered: { bytes: number | null; lowerBound: boolean; at: string } | null;
  /** The last walk of this scope that observed the folder. */
  reading: Reading | null;
  /** Continuation pages from this exact walk, kept with the level while descending. */
  pages: Reading[];
  pageAsking: boolean;
  pageProblem: string | null;
  /** The latest walk, when it did not observe; the observed one above stays visible. */
  failure: Reading | null;
  /** The latest request, when it never answered. */
  problem: string | null;
  asking: boolean;
  request: number;
  /** Returned to from a deeper level: the visible walk was taken earlier. */
  restored: boolean;
  selected: string | null;
}

export interface SpaceViewState { levels: SpaceLevel[]; mode: SpaceMode }

const INITIAL_SPACE_VIEW: SpaceViewState = { levels: [], mode: 'tiles' };

export const VIEWS: { id: ViewId; label: string; group: ViewGroup }[] = [
  { id: 'home', label: 'Home', group: 'Cases' },
  { id: 'case', label: 'Case', group: 'Cases' },
  { id: 'crashes', label: 'Crashes', group: 'Readings' },
  { id: 'record', label: 'System log', group: 'Readings' },
  { id: 'errors', label: 'Hardware errors', group: 'Readings' },
  { id: 'signals', label: 'Leads', group: 'Readings' },
  { id: 'machine', label: 'Machine', group: 'Readings' },
  { id: 'performance', label: 'Performance', group: 'Readings' },
  { id: 'space', label: 'Space', group: 'Readings' },
  { id: 'diagnostics', label: 'Diagnostics', group: 'Readings' },
  { id: 'agents', label: 'Connect an agent', group: 'Agent' },
  { id: 'stack', label: 'Stack (before cases)', group: 'Agent' },
];

const CASE_ID = /^[A-Za-z0-9_-]{1,64}$/;

/** The address carries the visible view and a held investigation moment, never a credential. */
function navigationFromAddress(): { view: ViewId; moment: string | null; caseId: string | null } {
  const query = new URLSearchParams(window.location.search);
  const requested = query.get('view');
  const asked = query.get('case');
  const caseId = asked && CASE_ID.test(asked) ? asked : null;
  const named = VIEWS.find((item) => item.id === requested)?.id ?? 'home';
  const view = named === 'case' && !caseId ? 'home' : named;
  const candidate = query.get('moment');
  const moment = candidate && candidate.length <= 64 && /^\d{4}-\d{2}-\d{2}T/.test(candidate) ? qualifiedMoment(candidate) : null;
  return { view, moment, caseId };
}

function qualifiedMoment(value: string): string | null {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed) || parsed < Date.UTC(1601, 0, 1)) return null;
  // The exact Windows window accepts seconds and at most seven fractional digits. Keep those
  // digits when the address already qualifies; canonicalize browser-only forms before a take.
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    ? value : new Date(parsed).toISOString();
}

function writeAddress(view: ViewId, moment: string | null, state: object | null = null) {
  const url = new URL(window.location.href);
  if (view === 'home') url.searchParams.delete('view');
  else url.searchParams.set('view', view);
  const caseId = useApp.getState().caseId;
  if (caseId) url.searchParams.set('case', caseId);
  else url.searchParams.delete('case');
  if (moment) url.searchParams.set('moment', moment);
  else url.searchParams.delete('moment');
  url.hash = '';
  history.pushState(state, '', url);
}

interface AppState {
  /** unknown until the catalog answers; closed on a 401, unreachable on a transport or server failure. */
  session: 'unknown' | 'open' | 'closed' | 'unreachable';
  setSession: (s: AppState['session']) => void;
  view: ViewId;
  setView: (v: ViewId) => void;
  /**
   * The case being worked. It stays in the address while the person goes to a reading for more
   * evidence, so every lens knows where "Add" puts things and a reload keeps the thread.
   */
  caseId: string | null;
  /** The held case's title, as its last answer named it, for the bar and the add control. */
  caseTitle: string | null;
  /** Hold a case without leaving this view: adding to a case from a lens makes it the one in hand. */
  holdCase: (id: string, title: string) => void;
  /** Open a case, or with null set the current one down and go home. */
  openCase: (id: string | null) => void;
  /** Keep working in this view but stop adding to the case. */
  releaseCase: () => void;
  /** Bumped after any case changes, so the rail, home and the case read the server again. */
  casesVersion: number;
  bumpCases: () => void;
  /** The last viewport position of each view in this tab, captured before navigation unmounts it. */
  viewScroll: Partial<Record<ViewId, number>>;
  /**
   * The moment the reader jumped to, held until it is cleared. Record frames the log around it;
   * leaving for another view and coming back finds the same frame, because a person who was
   * interrupted mid-investigation should not have to find the moment again.
   */
  moment: string | null;
  /** View that opened the current Record moment through an explicit evidence link. */
  recordOrigin: ViewId | null;
  /** Identity of the explicit moment control in the held source view, for precise return focus. */
  recordReturnKey: string | null;
  /**
   * Taking a moment is navigation, so it moves the view with it: one action, and the frame and the
   * place it belongs to can never disagree. Clearing it leaves the view where it is.
   */
  setMoment: (at: string | null, returnKey?: string) => void;
  /** Keep the Performance window and selected sample while its view is unmounted for a record jump. */
  performanceView: PerformanceViewState;
  setPerformanceView: (change: Partial<PerformanceViewState>) => void;
  /** Remember controls and source identities within this tab, never raw evidence or paths in the URL. */
  crashesView: CrashesViewState;
  setCrashesView: (change: Partial<CrashesViewState>) => void;
  signalId: string | null;
  setSignalId: (id: string | null) => void;
  /** The Space walk path, selection and mode: one navigator for both drawings, held in this tab. */
  spaceView: SpaceViewState;
  setSpaceView: (change: (current: SpaceViewState) => SpaceViewState) => void;
  clearViewContext: () => void;
  /** Restore a browser history entry without writing another entry. */
  restoreAddress: () => void;
}

const initial = navigationFromAddress();

export const useApp = create<AppState>((set, get) => ({
  session: 'unknown',
  setSession: (session) => set({ session }),
  view: initial.view,
  caseId: initial.caseId,
  caseTitle: null,
  holdCase: (id, title) => {
    const changed = id !== get().caseId;
    set({ caseId: id, caseTitle: title });
    if (changed) writeAddress(get().view, get().moment);
  },
  openCase: (id) => {
    const view: ViewId = id ? 'case' : 'home';
    set((state) => ({ caseId: id, caseTitle: id === state.caseId ? state.caseTitle : null, view, viewScroll: { ...state.viewScroll, [state.view]: window.scrollY } }));
    writeAddress(view, get().moment);
  },
  casesVersion: 0,
  bumpCases: () => set((state) => ({ casesVersion: state.casesVersion + 1 })),
  releaseCase: () => {
    set({ caseId: null, caseTitle: null });
    writeAddress(get().view, get().moment);
  },
  viewScroll: {},
  setView: (view) => {
    if (view === get().view) return;
    set((state) => ({ view, recordOrigin: view === 'record' ? null : state.recordOrigin,
      recordReturnKey: view === 'record' ? null : state.recordReturnKey,
      viewScroll: { ...state.viewScroll, [state.view]: window.scrollY } }));
    writeAddress(view, get().moment);
  },
  moment: initial.moment,
  recordOrigin: null,
  recordReturnKey: null,
  setMoment: (at, returnKey) => {
    at = at ? qualifiedMoment(at) : null;
    if (at === get().moment && (!at || get().view === 'record')) return;
    const from = get().view;
    set((state) => ({ ...(at ? { moment: at, view: 'record' as const,
      recordOrigin: state.view === 'record' ? state.recordOrigin : state.view,
      recordReturnKey: state.view === 'record' ? state.recordReturnKey : returnKey ?? null }
      : { moment: null, recordOrigin: null, recordReturnKey: null }),
      viewScroll: state.view === 'record' ? state.viewScroll : { ...state.viewScroll, [state.view]: window.scrollY } }));
    writeAddress(get().view, at, at && from !== 'record' ? { sentinelReturnTo: from } : null);
  },
  performanceView: { hours: 6, endChoice: 'now', selectedAt: null },
  setPerformanceView: (change) => set((state) => ({ performanceView: { ...state.performanceView, ...change } })),
  crashesView: INITIAL_CRASHES_VIEW,
  setCrashesView: (change) => set((state) => ({ crashesView: { ...state.crashesView, ...change } })),
  signalId: null,
  setSignalId: (signalId) => set({ signalId }),
  spaceView: INITIAL_SPACE_VIEW,
  setSpaceView: (change) => set((state) => ({ spaceView: change(state.spaceView) })),
  clearViewContext: () => set({ viewScroll: {}, crashesView: { ...INITIAL_CRASHES_VIEW }, signalId: null, recordOrigin: null, recordReturnKey: null, spaceView: INITIAL_SPACE_VIEW }),
  restoreAddress: () => set((state) => ({ ...navigationFromAddress(), viewScroll: { ...state.viewScroll, [state.view]: window.scrollY } })),
}));
