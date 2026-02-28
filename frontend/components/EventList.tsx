import React from 'react';
import { SystemLog, WheaLog } from '@/lib/types';
import { AlertCircle, AlertTriangle, Info, CheckCircle, Cpu } from 'lucide-react';
import { useSelectionStore } from '@/lib/selectionStore';
import { parseDate, formatDateTime } from '@/lib/dateUtils';

interface EventListProps {
    systemEvents: SystemLog[];
    wheaEvents: WheaLog[];
    filter: 'all' | 'system' | 'whea';
    onSelect: (item: SystemLog | WheaLog | null) => void;
    selectedId: string | number | null;
}

export const EventList: React.FC<EventListProps> = ({ systemEvents, wheaEvents, filter, onSelect, selectedId }) => {
    const { select, isSelected, selectedIds } = useSelectionStore();

    // Merge and sort
    const allEvents = React.useMemo(() => {
        const combined = [
            ...systemEvents.map((e, idx) => ({ ...e, type: 'system' as const, uniqueId: (e as any)._uuid || `sys-${e.Id}-${e.TimeCreated}-${idx}` })),
            ...wheaEvents.map((e, idx) => ({ ...e, type: 'whea' as const, uniqueId: (e as any)._uuid || `whea-${e.Id}-${e.TimeCreated}-${idx}` }))
        ];
        // Sort desc by time
        return combined.sort((a, b) => {
            const dateA = parseDate(a.TimeCreated).getTime();
            const dateB = parseDate(b.TimeCreated).getTime();
            return dateB - dateA;
        });
    }, [systemEvents, wheaEvents]);

    const filtered = allEvents.filter(e => {
        if (filter === 'all') return true;
        return e.type === filter;
    });

    // Create an array of IDs for range selection
    const allIds = filtered.map(e => e.uniqueId);

    const handleRowClick = (e: React.MouseEvent, event: any) => {
        // Multi-select logic
        select(event.uniqueId, {
            shift: e.shiftKey,
            ctrl: e.ctrlKey || e.metaKey
        }, allIds);

        // Original single-select behavior (for Inspector)
        // Only trigger if we are actively selecting a SINGLE item without modifiers, 
        // OR we might want inspector to follow the *last* selected item?
        // Let's keep simpler: always notify parent of the clicked item so inspector updates.
        onSelect(event);

        // Prevent text selection
        if (e.shiftKey) {
            window.getSelection()?.removeAllRanges();
        }
    };

    return (
        <div className="flex-1 overflow-y-auto custom-scrollbar">
            <div className="flex flex-col">
                {filtered.map((event) => {
                    const selected = isSelected(event.uniqueId);
                    const isWhea = event.type === 'whea';

                    // Icon selection
                    let Icon = AlertCircle;
                    let iconColor = 'text-critical';

                    if (isWhea) {
                        Icon = Cpu;
                        iconColor = 'text-warning';
                    } else if (event.LevelDisplayName === 'Error') {
                        Icon = AlertCircle;
                        iconColor = 'text-critical';
                    } else if (event.LevelDisplayName === 'Warning') {
                        Icon = AlertTriangle;
                        iconColor = 'text-warning';
                    }

                    return (
                        <div
                            key={event.uniqueId}
                            onClick={(e) => handleRowClick(e, event)}
                            className={`
                        group flex items-center gap-3 px-4 py-3 border-b border-subtle cursor-pointer transition-all
                        ${selected
                                    ? 'bg-surface-active'
                                    : 'hover:bg-surface-1 border-subtle'}
                    `}
                        >
                            <Icon className={`w-4 h-4 shrink-0 ${iconColor}`} />

                            <div className="flex-1 min-w-0">
                                <div className="flex justify-between items-baseline mb-0.5">
                                    <span className={`text-sm font-medium truncate pr-2 ${selected ? 'text-primary' : 'text-primary'}`}>
                                        {isWhea ? 'Hardware Error' : event.ProviderName}
                                    </span>
                                    <span className="text-xs text-secondary font-mono shrink-0">
                                        {formatDateTime(parseDate(event.TimeCreated))}
                                    </span>
                                </div>
                                <div className={`text-xs truncate ${selected ? 'text-secondary' : 'text-secondary'}`}>
                                    {event.Message}
                                </div>
                            </div>
                        </div>
                    );
                })}
                {filtered.length === 0 && (
                    <div className="p-8 text-center text-tertiary text-sm">
                        No events found for this filter.
                    </div>
                )}
            </div>
        </div>
    );
};
