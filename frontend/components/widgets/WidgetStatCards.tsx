import React from 'react';
import { Activity, Cpu } from 'lucide-react';
import { useSystemStore } from '../../lib/store';

export const WidgetActiveIncidents: React.FC = () => {
    const events = useSystemStore(state => state.events);
    // Count tier-0 events (Kernel-Power 41, etc)
    const crashCount = events.filter(e => [41, 6008, 1001].includes(e.eventId)).length;

    return (
        <div className="bg-surface-1 border border-subtle rounded-lg p-4 flex flex-col gap-3 h-64">
            <div className="flex items-center gap-2 text-secondary text-sm font-medium uppercase tracking-wider">
                <Activity className="w-4 h-4" />
                <span>Active Incidents (24h)</span>
            </div>
            <div className="flex flex-col items-center justify-center h-full gap-2">
                <span className="text-4xl font-bold text-primary">{crashCount}</span>
                <span className="text-secondary text-sm">Critical Interruptions</span>
            </div>
        </div>
    );
};

export const WidgetWheaBurst: React.FC = () => {
    const events = useSystemStore(state => state.events);
    const wheaCount = events.filter(e => e.provider === 'Microsoft-Windows-WHEA-Logger').length;

    return (
        <div className="bg-surface-1 border border-subtle rounded-lg p-4 flex flex-col gap-3 h-64">
            <div className="flex items-center gap-2 text-secondary text-sm font-medium uppercase tracking-wider">
                <Cpu className="w-4 h-4" />
                <span>WHEA Burst Detector</span>
            </div>
            <div className="flex flex-col items-center justify-center h-full gap-2">
                <span className={`text-4xl font-bold ${wheaCount > 0 ? 'text-red-500' : 'text-primary'}`}>{wheaCount}</span>
                <span className="text-secondary text-sm">Hardware Errors</span>
            </div>
        </div>
    );
};
