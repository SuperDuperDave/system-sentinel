import { create } from 'zustand';

export type ViewId = 'record' | 'errors' | 'dumps' | 'machine' | 'diagnostics' | 'signals' | 'stack' | 'agents';

/** The views, in the order the nav shows them. The studio page copies these names; change them there too. */
export const VIEWS: { id: ViewId; label: string }[] = [
  { id: 'record', label: 'Record' },
  { id: 'errors', label: 'Hardware errors' },
  { id: 'dumps', label: 'Crash dumps' },
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
}

export const useApp = create<AppState>((set) => ({
  session: 'unknown',
  setSession: (session) => set({ session }),
  view: 'record',
  setView: (view) => set({ view }),
}));
