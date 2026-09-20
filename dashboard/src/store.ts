import { create } from 'zustand';

export type ViewId = 'record';

export const VIEWS: { id: ViewId; label: string }[] = [{ id: 'record', label: 'Record' }];

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
