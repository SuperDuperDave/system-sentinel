import React from 'react';
import { AlertTriangle, Clock } from 'lucide-react';
import { useSystemStore } from '../../lib/store';

export const WidgetRecentCritical: React.FC = () => {
    const events = useSystemStore(state => state.events);
    // Filter for Level=Critical (1) or Error (2) or their string representations
    // Note: PowerShell LevelDisplayName usually returns "Error", "Critical", etc.
    const criticalEvents = events
        .filter(e => ['Critical', 'Error', '1', '2'].includes(e.level))
        .slice(0, 5);

    return (
        <div className="bg-surface-1 border border-subtle rounded-lg p-4 flex flex-col gap-3 h-64">
            <div className="flex items-center justify-between text-secondary text-sm font-medium uppercase tracking-wider">
                <div className="flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-red-500" />
                    <span>Live Critical Events</span>
                </div>
                {/* Connection Dot */}
                <div className="flex items-center gap-1">
                    <div className={`w-2 h-2 rounded-full ${useSystemStore.getState().streamStatus === 'connected' ? 'bg-green-500 shadow-glow-green' : 'bg-red-500'}`}></div>
                </div>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar -mr-2 pr-2">
                <div className="flex flex-col gap-2">
                    {criticalEvents.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-secondary gap-2 opacity-50">
                            <span className="text-sm">System Nominal</span>
                        </div>
                    ) : (
                        criticalEvents.map((e, i) => (
                            <div key={i} className="flex gap-3 text-sm border-b border-subtle pb-2 last:border-0 hover:bg-surface-2 p-1.5 rounded transition-colors cursor-pointer group">
                                <div className="mt-1.5 shrink-0">
                                    <div className="w-2 h-2 rounded-full bg-red-500 shadow-glow-red"></div>
                                </div>
                                <div className="flex flex-col min-w-0 w-full">
                                    <div className="flex items-center justify-between">
                                        <span className="font-mono text-xs text-red-400">ID {e.eventId}</span>
                                        <div className="flex items-center gap-1 text-xs text-tertiary">
                                            <Clock className="w-3 h-3" />
                                            <span>{new Date(e.timeCreated).toLocaleTimeString()}</span>
                                        </div>
                                    </div>
                                    <div className="flex items-center justify-between gap-2 mt-0.5">
                                        <span className="text-secondary text-xs truncate max-w-[120px]">{e.provider}</span>
                                    </div>
                                    <span className="text-primary truncate mt-0.5" title={e.message}>{e.message || "No description"}</span>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>
    );
};
