import { create } from 'zustand';

export type ViewId = 'record' | 'errors' | 'crashes' | 'machine' | 'diagnostics' | 'signals' | 'stack' | 'agents';

/** The views, in the order the nav shows them. The studio page copies these names; change them there too. */
export const VIEWS: { id: ViewId; label: string }[] = [
  { id: 'record', label: 'Record' },
  { id: 'errors', label: 'Hardware errors' },
  { id: 'crashes', label: 'Crashes' },
  { id: 'machine', label: 'Machine' },
  { id: 'diagnostics', label: 'Diagnostics' },
  { id: 'signals', label: 'Signals' },
  { id: 'stack', label: 'Stack' },
  { id: 'agents', label: 'Agents' },
];

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
}

export const useApp = create<AppState>((set) => ({
  session: 'unknown',
  setSession: (session) => set({ session }),
  view: 'record',
  setView: (view) => set({ view }),
  moment: null,
  setMoment: (at) => set(at ? { moment: at, view: 'record' } : { moment: null }),
}));
