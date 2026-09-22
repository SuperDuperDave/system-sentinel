import { create } from 'zustand';

export type ViewId = 'record' | 'errors' | 'crashes' | 'machine' | 'diagnostics' | 'signals' | 'stack' | 'agents';

/** The views, in the order the nav shows them. The studio page copies these names; change them there too. */
export type ViewGroup = 'Evidence' | 'Interpret' | 'Carry';

export const VIEWS: { id: ViewId; label: string; group: ViewGroup }[] = [
  { id: 'record', label: 'Record', group: 'Evidence' },
  { id: 'errors', label: 'Hardware errors', group: 'Evidence' },
  { id: 'crashes', label: 'Crashes', group: 'Evidence' },
  { id: 'machine', label: 'Machine', group: 'Evidence' },
  { id: 'diagnostics', label: 'Diagnostics', group: 'Interpret' },
  { id: 'signals', label: 'Signals', group: 'Interpret' },
  { id: 'stack', label: 'Stack', group: 'Carry' },
  { id: 'agents', label: 'Agents', group: 'Carry' },
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
