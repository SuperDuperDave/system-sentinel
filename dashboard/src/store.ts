import { create } from 'zustand';

export type ViewId = 'record' | 'errors' | 'crashes' | 'machine' | 'performance' | 'diagnostics' | 'signals' | 'stack' | 'agents';

/** The views, in the order the nav shows them. The studio page copies these names; change them there too. */
export type ViewGroup = 'Evidence' | 'Interpret' | 'Carry';

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
  /** Which open evidence to return to after following its System record. */
  focus: 'stop' | 'fault' | 'dump' | null;
}

const INITIAL_CRASHES_VIEW: CrashesViewState = { stopCount: 5, faultCount: 30, faultKind: null,
  stopId: null, faultId: null, dumpId: null, reliabilityDay: null, focus: null };

export const VIEWS: { id: ViewId; label: string; group: ViewGroup }[] = [
  { id: 'record', label: 'Record', group: 'Evidence' },
  { id: 'errors', label: 'Hardware errors', group: 'Evidence' },
  { id: 'crashes', label: 'Crashes', group: 'Evidence' },
  { id: 'machine', label: 'Machine', group: 'Evidence' },
  { id: 'performance', label: 'Performance', group: 'Evidence' },
  { id: 'diagnostics', label: 'Diagnostics', group: 'Interpret' },
  { id: 'signals', label: 'Signals', group: 'Interpret' },
  { id: 'stack', label: 'Stack', group: 'Carry' },
  { id: 'agents', label: 'Agents', group: 'Carry' },
];

/** The address carries the visible view and a held investigation moment, never a credential. */
function navigationFromAddress(): { view: ViewId; moment: string | null } {
  const query = new URLSearchParams(window.location.search);
  const requested = query.get('view');
  const view = VIEWS.find((item) => item.id === requested)?.id ?? 'record';
  const candidate = query.get('moment');
  const moment = candidate && candidate.length <= 64 && /^\d{4}-\d{2}-\d{2}T/.test(candidate) ? qualifiedMoment(candidate) : null;
  return { view, moment };
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
  if (view === 'record') url.searchParams.delete('view');
  else url.searchParams.set('view', view);
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
  clearViewContext: () => void;
  /** Restore a browser history entry without writing another entry. */
  restoreAddress: () => void;
}

const initial = navigationFromAddress();

export const useApp = create<AppState>((set, get) => ({
  session: 'unknown',
  setSession: (session) => set({ session }),
  view: initial.view,
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
  clearViewContext: () => set({ viewScroll: {}, crashesView: { ...INITIAL_CRASHES_VIEW }, signalId: null, recordOrigin: null, recordReturnKey: null }),
  restoreAddress: () => set((state) => ({ ...navigationFromAddress(), viewScroll: { ...state.viewScroll, [state.view]: window.scrollY } })),
}));
