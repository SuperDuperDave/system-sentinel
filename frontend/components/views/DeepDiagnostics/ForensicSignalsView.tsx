import React, { useMemo, useState } from 'react';
import { useSystemStore } from '@/lib/store';
import { useContextStore } from '../../context/ContextStore';
import { useSelectionStore } from '@/lib/selectionStore';
import { Search, Shield, TrendingUp, AlertTriangle, Gauge, Activity, ExternalLink, PlusCircle, MinusCircle, Zap, CheckCircle, Clock, Plus, Minus, AlertOctagon } from 'lucide-react';
import { SmartContextControl } from '../../context/SmartContextControl';

const ForensicSignalsView: React.FC = () => {
    const signals = useSystemStore(state => state.diagnosticReport?.domains?.forensic_signals);
    const { addItem, items, removeItemsBySourceId, removeItem } = useContextStore();
    const { setInspectableItem } = useSystemStore();
    const { select, isSelected, selectedIds, clearSelection } = useSelectionStore();

    // Local filter state
    const [selectedBucket, setSelectedBucket] = useState<string | null>(null);

    // Data Access
    const buckets = signals?.buckets || {};
    const rawRanked = signals?.ranked || [];

    // --- Helper: Bucket Icon Map ---
    const bucketConfig: Record<string, { icon: any, color: string, bg: string, border: string, label: string }> = {
        suppressions: { icon: Shield, color: 'text-amber-500', bg: 'bg-amber-500/10', border: 'border-amber-500/20', label: "Suppressions" },
        gaps: { icon: AlertTriangle, color: 'text-red-500', bg: 'bg-red-500/10', border: 'border-red-500/20', label: "Broken Dependencies" },
        pressure: { icon: Activity, color: 'text-purple-500', bg: 'bg-purple-500/10', border: 'border-purple-500/20', label: "Signal Pressure" },
        transitions: { icon: Clock, color: 'text-blue-500', bg: 'bg-blue-500/10', border: 'border-blue-500/20', label: "Transition Correlations" },
        mismatches: { icon: AlertOctagon, color: 'text-orange-500', bg: 'bg-orange-500/10', border: 'border-orange-500/20', label: "Config Mismatches" },
    };

    const isAdded = (id: string) => items.some(item => item.data?.id === id);

    // Filter Logic
    const ranked = useMemo(() => {
        if (!selectedBucket) return rawRanked;
        return rawRanked.filter((s: any) => s.class === selectedBucket);
    }, [rawRanked, selectedBucket]);

    // Helper to robustly parse dates (handling ASP.NET /Date()/ format)
    const parseDate = (dateStr: any) => {
        if (!dateStr) return null;
        if (typeof dateStr === 'string' && dateStr.includes('/Date(')) {
            const match = dateStr.match(/\/Date\((\d+)\)\//);
            if (match) return new Date(parseInt(match[1]));
        }
        const d = new Date(dateStr);
        return isNaN(d.getTime()) ? null : d;
    };

    if (!signals) {
        return (
            <div className="flex items-center justify-center h-full text-secondary">
                <p>No forensic signals available. Run a Deep Scan.</p>
            </div>
        );
    }

    // 1. Assign stable IDs to ALL events based on global index
    // This matches Dashboard.tsx logic and ensures uniqueness even with identical timestamps
    const allEventsWithIds = useMemo(() => {
        return (signals?.timeline?.events || []).map((e: any, globalIdx: number) => ({
            ...e,
            uniqueId: `timeline-${e.ProviderName}-${e.Id}-${e.TimeCreated}-${globalIdx}`,
            TimeCreated: e.TimeCreated
        }));
    }, [signals?.timeline?.events]);

    // 2. Filter the stable events based on selected bucket
    const displayEvents = useMemo(() => {
        if (!selectedBucket) return allEventsWithIds;

        // Filter valid events same as before, but preserving the ID
        if (selectedBucket === 'gaps' || selectedBucket === 'pressure') {
            return allEventsWithIds.filter((e: any) =>
                e.LevelDisplayName === 'Error' ||
                e.LevelDisplayName === 'Critical' ||
                e.LevelDisplayName === 'Warning'
            );
        }
        return allEventsWithIds;
    }, [allEventsWithIds, selectedBucket]);

    const allTimelineIds = useMemo(() => displayEvents.map((e: any) => e.uniqueId), [displayEvents]);

    const handleBucketClick = (bucket: string) => {
        setSelectedBucket(prev => prev === bucket ? null : bucket);
        clearSelection(); // Clear selection when changing filter
    };

    const toggleContext = (e: React.MouseEvent, signal: any) => {
        e.stopPropagation();
        if (isAdded(signal.id)) {
            const found = items.find(item => item.data?.id === signal.id);
            if (found) removeItem(found.id);
        } else {
            addItem({
                type: 'evidence',
                title: signal.title,
                data: signal,
                description: signal.summary
            });
        }
    };

    // Check if timeline events are in context stack
    const selectedTimelineEvents = useMemo(() => {
        if (!displayEvents) return [];
        return displayEvents.filter((evt: any) =>
            isSelected(evt.uniqueId)
        );
    }, [displayEvents, selectedIds]);

    // Check if selection is in context
    const selectionInContext = useMemo(() => {
        return selectedTimelineEvents.every((evt: any) => {
            const sourceId = evt.uniqueId; // Simplified: sourceId IS the uniqueId now
            return items.some(item => item.sourceId === sourceId);
        });
    }, [selectedTimelineEvents, items]);

    // Add/remove selected timeline events
    const handleContextStackAction = () => {
        if (selectionInContext) {
            // Remove
            selectedTimelineEvents.forEach((evt: any) => {
                removeItemsBySourceId([evt.uniqueId]);
            });
        } else {
            // Add
            selectedTimelineEvents.forEach((evt: any) => {
                if (!items.some(item => item.sourceId === evt.uniqueId)) {
                    addItem({
                        type: 'evidence',
                        title: `Event ${evt.Id} - ${evt.ProviderName}`,
                        data: evt,
                        evidenceClass: 'raw',
                        rank: 2,
                        sourceId: evt.uniqueId
                    });
                }
            });
        }
    };

    return (
        <div className="flex flex-col h-full w-full overflow-hidden bg-bg-app">

            {/* Header */}
            <header className="px-6 py-4 border-b border-subtle flex items-center justify-between bg-surface-1 shrink-0">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-purple-500/10 rounded-lg">
                        <Search className="w-5 h-5 text-purple-500" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-primary">Forensic Signals</h1>
                        <p className="text-xs text-secondary">Signal Intelligence • Anomaly Detection • Diagnostic Probability</p>
                    </div>
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={() => addItem({ type: 'evidence', title: "Full Forensic Signals", data: signals })}
                        className="px-3 py-1.5 bg-surface-2 hover:bg-surface-3 border border-subtle rounded text-xs font-bold text-secondary transition-colors"
                    >
                        Export Analysis
                    </button>
                </div>
            </header>

            <div className="p-6 overflow-y-auto custom-scrollbar space-y-8">

                {/* 1. Signal Radar (Tiles) */}
                <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                    {Object.entries(bucketConfig).map(([key, config]) => {
                        const count = (buckets[key] || []).length;
                        const validCount = count > 0;
                        const MyIcon = config.icon;
                        const isSelected = selectedBucket === key;

                        return (
                            <div
                                key={key}
                                onClick={() => handleBucketClick(key)}
                                className={`
                                    relative p-4 rounded-xl border transition-all cursor-pointer overflow-hidden
                                    ${isSelected
                                        ? `bg-surface-2 ${config.border} ring-1 ring-offset-1 ring-offset-bg-app ring-${config.color.split('-')[1]}-500`
                                        : 'bg-surface-1 border-subtle hover:border-text-secondary/20 hover:bg-surface-2'
                                    }
                                `}
                            >
                                <div className="flex items-start justify-between mb-2">
                                    <div className={`p-2 rounded-lg ${config.bg}`}>
                                        <MyIcon className={`w-5 h-5 ${config.color}`} />
                                    </div>
                                    <span className={`text-2xl font-mono font-bold ${validCount ? 'text-primary' : 'text-tertiary'}`}>
                                        {count}
                                    </span>
                                </div>
                                <div className="space-y-1">
                                    <h3 className={`text-xs font-bold uppercase tracking-wider ${validCount ? 'text-secondary' : 'text-tertiary'}`}>
                                        {config.label}
                                    </h3>
                                    {isSelected && (
                                        <div className="absolute top-0 right-0 p-1.5">
                                            <CheckCircle className={`w-3 h-3 ${config.color}`} />
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>

                {/* 2. Top Signals (Ranked) */}
                {ranked.length > 0 && (
                    <section>
                        <h2 className="text-xs font-bold text-tertiary uppercase tracking-widest mb-4">
                            {selectedBucket ? `Analysis: ${bucketConfig[selectedBucket]?.label}` : 'Top Forensic Signals'}
                        </h2>
                        <div className="grid grid-cols-1 gap-3">
                            {ranked.slice(0, 5).map((signal: any) => {
                                const config = bucketConfig[signal.class] || bucketConfig['pressure'];
                                const Icon = config.icon;
                                const added = isAdded(signal.id);

                                return (
                                    <div
                                        key={signal.id}
                                        className="group relative flex items-start gap-4 p-4 bg-surface-1 rounded-xl border border-subtle hover:border-primary/20 transition-all"
                                    >
                                        <div className={`mt-1 p-2 rounded-lg shrink-0 ${config.bg}`}>
                                            <Icon className={`w-5 h-5 ${config.color}`} />
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-start justify-between gap-4">
                                                <div>
                                                    <h3 className="text-sm font-bold text-primary mb-1">{signal.title}</h3>
                                                    <p className="text-xs text-secondary leading-relaxed line-clamp-2">{signal.summary}</p>
                                                </div>
                                                <button
                                                    onClick={(e) => toggleContext(e, signal)}
                                                    className={`
                                                        p-1.5 rounded-full transition-all shrink-0 cursor-pointer
                                                        ${added
                                                            ? 'opacity-100 bg-red-500/10 text-red-500 hover:bg-red-500/20'
                                                            : 'opacity-0 group-hover:opacity-100 bg-transparent text-blue-500 hover:text-blue-400 hover:bg-blue-500/10'
                                                        }
                                                    `}
                                                    title={added ? "Remove from Context" : "Add to Context"}
                                                >
                                                    {added ? <MinusCircle size={20} /> : <PlusCircle size={20} />}
                                                </button>
                                            </div>

                                            {/* Evidence Tags */}
                                            <div className="mt-3 flex flex-wrap gap-2">
                                                <span className={`px-2 py-1 rounded text-[10px] uppercase font-bold tracking-wider ${config.bg} ${config.color} bg-opacity-30`}>
                                                    {config.label}
                                                </span>
                                                {Object.entries(signal.evidence || {}).slice(0, 2).map(([k, v]: any) => (
                                                    <span key={k} className="px-2 py-1 rounded bg-bg-app border border-subtle text-[10px] text-tertiary font-mono">
                                                        {k}: {String(v).substring(0, 20)}
                                                    </span>
                                                ))}
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </section>
                )}

                {/* 3. Timeline / Ledger */}
                {displayEvents.length > 0 && (
                    <section>
                        <div className="bg-surface-1 border border-subtle rounded-xl flex flex-col overflow-hidden min-h-[300px]">
                            <div className="p-4 border-b border-subtle flex items-center justify-between shrink-0">
                                <div className="flex items-center gap-3">
                                    <Clock className="w-4 h-4 text-tertiary" />
                                    <h3 className="font-bold text-sm text-primary">Signal Ledger (Timeline)</h3>
                                </div>
                            </div>
                            <div className="bg-surface-1 flex flex-col border border-subtle rounded-xl overflow-hidden bg-bg-app"> {/* Container border matching EventList context usually */}
                                {displayEvents.map((evt: any, idx: number) => {
                                    const selected = isSelected(evt.uniqueId);

                                    // Determine Icon
                                    let Icon = Activity;
                                    let iconColor = 'text-blue-500';
                                    const lvl = (evt.LevelDisplayName || '').toLowerCase();
                                    if (lvl === 'error' || lvl === 'critical') {
                                        Icon = AlertTriangle;
                                        iconColor = 'text-red-500';
                                    } else if (lvl === 'warning') {
                                        Icon = AlertTriangle;
                                        iconColor = 'text-yellow-500';
                                    }

                                    const parsedDate = parseDate(evt.TimeCreated);

                                    return (
                                        <div
                                            key={evt.uniqueId}
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                // Multi-select with Shift/Ctrl
                                                select(evt.uniqueId, {
                                                    ctrl: e.ctrlKey || e.metaKey,
                                                    shift: e.shiftKey
                                                }, allTimelineIds); // Pass all IDs for range selection

                                                setInspectableItem({
                                                    uniqueId: evt.uniqueId,
                                                    type: 'timeline_event',
                                                    title: `Event ${evt.Id} - ${evt.ProviderName}`,
                                                    data: evt, // Pass full object for inspector
                                                    ...evt
                                                });
                                            }}
                                            className={`group flex items-center gap-3 px-4 py-3 border-b border-subtle cursor-pointer transition-all ${selected
                                                ? 'bg-surface-active'
                                                : 'hover:bg-surface-1 border-subtle'
                                                }`}
                                        >
                                            <Icon className={`w-4 h-4 shrink-0 ${iconColor}`} />

                                            <div className="flex-1 min-w-0">
                                                <div className="flex justify-between items-baseline mb-0.5">
                                                    <span className={`text-sm font-medium truncate pr-2 ${selected ? 'text-primary' : 'text-primary'}`}>
                                                        {evt.ProviderName}
                                                    </span>
                                                    <span className="text-xs text-secondary font-mono shrink-0">
                                                        {parsedDate
                                                            ? parsedDate.toLocaleString('en-US', {
                                                                month: '2-digit',
                                                                day: '2-digit',
                                                                year: 'numeric',
                                                                hour: '2-digit',
                                                                minute: '2-digit',
                                                                hour12: true
                                                            })
                                                            : 'Unknown'
                                                        }
                                                    </span>
                                                </div>
                                                <div className="text-xs truncate text-secondary">
                                                    {evt.Message}
                                                </div>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>

                            {/* Window Info */}
                            <div className="mt-4 pt-4 border-t border-subtle/50 text-center text-[10px] text-tertiary">
                                Window: Last {signals.timeline.window_minutes} minutes • {displayEvents.length} events
                                {selectedBucket && <span className="text-blue-500"> (filtered by {bucketConfig[selectedBucket]?.label})</span>}
                            </div>
                        </div>
                    </section>
                )}

                {/* No timeline data fallback */}
                {(!displayEvents || displayEvents.length === 0) && (
                    <div className="border-t border-subtle pt-8 opacity-40">
                        <h2 className="text-xs font-bold text-tertiary uppercase tracking-widest mb-4">Signal Ledger (Timeline)</h2>
                        <div className="h-32 bg-surface-2 rounded-2xl border border-dashed border-subtle flex items-center justify-center">
                            <span className="text-[10px] font-bold">No timeline data available. Run a Deep Scan.</span>
                        </div>
                    </div>
                )}

            </div>



        </div>
    );
};

export default ForensicSignalsView;
