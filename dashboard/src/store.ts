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
  const moment = candidate && candidate.length <= 64 && /^\d{4}-\d{2}-\d{2}T/.test(candidate) && !Number.isNaN(Date.parse(candidate)) ? candidate : null;
  return { view, moment };
}

function writeAddress(view: ViewId, moment: string | null) {
  const url = new URL(window.location.href);
  if (view === 'record') url.searchParams.delete('view');
  else url.searchParams.set('view', view);
  if (moment) url.searchParams.set('moment', moment);
  else url.searchParams.delete('moment');
  url.hash = '';
  history.pushState(null, '', url);
}

interface AppState {
  /** unknown until the first request answers; closed on any 401. */
  session: 'unknown' | 'open' | 'closed';
  setSession: (s: AppState['session']) => void;
  view: ViewId;
  setView: (v: ViewId) => void;
  /**
   * The moment the reader jumped to, held until it is cleared. Record frames the log around it;
   * leaving for another view and coming back finds the same frame, because a person who was
   * interrupted mid-investigation should not have to find the moment again.
   */
  moment: string | null;
  /**
   * Taking a moment is navigation, so it moves the view with it: one action, and the frame and the
   * place it belongs to can never disagree. Clearing it leaves the view where it is.
   */
  setMoment: (at: string | null) => void;
  /** Keep the Performance window and selected sample while its view is unmounted for a record jump. */
  performanceView: PerformanceViewState;
  setPerformanceView: (change: Partial<PerformanceViewState>) => void;
  /** Restore a browser history entry without writing another entry. */
  restoreAddress: () => void;
}

const initial = navigationFromAddress();

export const useApp = create<AppState>((set, get) => ({
  session: 'unknown',
  setSession: (session) => set({ session }),
  view: initial.view,
  setView: (view) => {
    if (view === get().view) return;
    set({ view });
    writeAddress(view, get().moment);
  },
  moment: initial.moment,
  setMoment: (at) => {
    if (at === get().moment && (!at || get().view === 'record')) return;
    set(at ? { moment: at, view: 'record' } : { moment: null });
    writeAddress(get().view, at);
  },
  performanceView: { hours: 6, endChoice: 'now', selectedAt: null },
  setPerformanceView: (change) => set((state) => ({ performanceView: { ...state.performanceView, ...change } })),
  restoreAddress: () => set(navigationFromAddress()),
}));
