import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { SentinelEvent } from './types';

interface SystemStore {
    events: SentinelEvent[];
    liveFeedEnabled: boolean;
    streamStatus: 'connected' | 'disconnected' | 'reconnecting';
    lastHeartbeat: string | null;
    activeView: string;
    hardwareSnapshot: any | null;

    toggleLiveFeed: () => void;
    setStreamStatus: (status: 'connected' | 'disconnected' | 'reconnecting') => void;
    addEvent: (event: SentinelEvent) => void;
    setHeartbeat: (ts: string) => void;
    setActiveView: (view: string) => void;
    setHardwareSnapshot: (data: any) => void;
    diagnosticReport: any | null;
    inspectableItem: any | null; // For manually opening the Inspector
    apiBaseUrl: string; // Configurable backend URL
    setDiagnosticReport: (data: any) => void;
    setInspectableItem: (item: any | null) => void;
    setApiBaseUrl: (url: string) => void;
}

export const useSystemStore = create<SystemStore>((set) => ({
    events: [],
    liveFeedEnabled: false,
    streamStatus: 'disconnected',
    lastHeartbeat: null,
    activeView: 'overview',
    hardwareSnapshot: null,

    setActiveView: (view) => set({ activeView: view }),
    toggleLiveFeed: () => set(state => ({ liveFeedEnabled: !state.liveFeedEnabled })),
    setStreamStatus: (status) => set({ streamStatus: status }),
    addEvent: (event) => set(state => {
        // Robust deduplication
        const exists = state.events.some(e => {
            if (e.recordId && event.recordId) {
                return e.recordId === event.recordId && e.logName === event.logName;
            }
            // Fallback to strict uniqueId check or content hash match
            return e.uniqueId === event.uniqueId;
        });

        if (exists) return state;

        // Keep last 2000 events
        const newEvents = [event, ...state.events].slice(0, 2000);
        return { events: newEvents };
    }),
    setHeartbeat: (ts) => set({ lastHeartbeat: ts }),
    setHardwareSnapshot: (data) => set({ hardwareSnapshot: data }),
    diagnosticReport: null,
    inspectableItem: null,
    apiBaseUrl: typeof window !== 'undefined' ? (localStorage.getItem('apiBaseUrl') || 'http://localhost:8000') : 'http://localhost:8000',
    setDiagnosticReport: (data) => set({ diagnosticReport: data }),
    setInspectableItem: (item) => set({ inspectableItem: item }),
    setApiBaseUrl: (url) => {
        if (typeof window !== 'undefined') {
            localStorage.setItem('apiBaseUrl', url);
        }
        set({ apiBaseUrl: url });
    }
}));

interface DashboardStore {
    widgets: Record<string, boolean>;
    order: string[];
    toggleWidget: (id: string) => void;
    setOrder: (order: string[]) => void;
}

export const useDashboardStore = create(
    persist<DashboardStore>(
        (set) => ({
            widgets: {
                'active_incidents': true,
                'recent_critical': true,
                'whea_burst': true,
                'system_snapshot': true,
                'driver_changes': false
            },
            order: ['active_incidents', 'recent_critical', 'whea_burst', 'system_snapshot', 'driver_changes'],
            toggleWidget: (id) => set(state => ({
                widgets: { ...state.widgets, [id]: !state.widgets[id] }
            })),
            setOrder: (order) => set({ order })
        }),
        {
            name: 'dashboard-storage',
            version: 1,
        }
    )
);
