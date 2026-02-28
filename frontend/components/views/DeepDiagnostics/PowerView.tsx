import React, { useState } from 'react';
import { useSystemStore } from '@/lib/store';
import { useContextStore } from '../../context/ContextStore';
import { Zap, Battery, BatteryCharging, Power, Clock, Info, CheckCircle, AlertTriangle, Activity, Bell, PlusCircle, Check, ListChecks } from 'lucide-react';

export const PowerView: React.FC = () => {
    const report = useSystemStore(state => state.diagnosticReport);
    const { setInspectableItem } = useSystemStore();
    const { addItem, items, removeItemsBySourceId } = useContextStore();

    // Local selection state
    const [selectedEventIds, setSelectedEventIds] = useState<Set<number>>(new Set());
    const [activeCard, setActiveCard] = useState<string | null>(null);

    // V3 Data Structure: report.domains.power
    // But verify where it lands. The backend returns keys `invariants`, `signals`, `transitions`.
    // Assuming keys are merged into `report.domains.power`.
    const powerData = report?.domains?.power || report?.power || {};

    if (!powerData || !powerData.invariants) {
        return (
            <div className="p-12 flex flex-col items-center justify-center text-secondary">
                <Zap className="w-12 h-12 mb-4 opacity-20" />
                <h2 className="text-xl font-bold mb-2">No Power Telemetry</h2>
                <p>Please run a Deep Scan to analyze power states.</p>
            </div>
        );
    }

    const { invariants, signals, transitions, derived } = powerData;
    const ledger = transitions?.ledger || [];

    // --- Context Helpers ---

    const getSourceId = (title: string) => `power-${title.toLowerCase().replace(/\s+/g, '-')}`;

    const handleToggleContext = (title: string, data: any, type: string = 'evidence') => {
        const sourceId = getSourceId(title);
        const exists = items.some(i => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: `Power: ${title}`,
                description: `Power Telemetry for ${title}`,
                data: data,
                evidenceClass: 'derived',
                rank: 3,
                sourceId: sourceId
            });
        }
    };

    const handleLedgerSelectionAdd = () => {
        const selectedEvents = ledger.filter((evt: any) => selectedEventIds.has(evt.id));
        if (selectedEvents.length === 0) return;

        const sourceId = `power-ledger-selection-${selectedEvents.map((e: any) => e.id).join('-')}`;
        // Dedup check? 
        // For ledger selections, maybe just always add new unique snapshot? 
        // Or if same selection exists? simple for now.

        addItem({
            type: 'evidence',
            title: `Power Events (${selectedEvents.length})`,
            description: `Selected forensic events from Power Ledger.`,
            data: selectedEvents,
            evidenceClass: 'log',
            rank: 4,
            sourceId: sourceId
        });
        setSelectedEventIds(new Set());
    };

    const handleAddAllLedger = () => {
        const sourceId = 'power-ledger-full';
        const exists = items.some(i => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
            return;
        }

        addItem({
            type: 'evidence',
            title: `Full Power Ledger`,
            description: `Complete list of ${ledger.length} recent power transitions.`,
            data: ledger,
            evidenceClass: 'log',
            rank: 4,
            sourceId: sourceId
        });
    };

    // --- Interaction Helpers ---

    const handleEventClick = (evt: any, isMulti: boolean) => {
        // 1. Inspector
        setInspectableItem({
            uniqueId: `power-evt-${evt.id}`,
            type: 'diagnostic', // Use diagnostic to trigger generic data view
            panelTitle: 'Event Details', // Sidebar Title
            title: evt.type, // Content Header
            description: evt.message || `${evt.type} event detected by ${evt.provider}`,
            LevelDisplayName: evt.type === 'Wake' ? 'Information' : (evt.type === 'Sleep' ? 'Information' : 'Critical'),
            ProviderName: evt.provider || 'PowerTransition',
            TimeCreated: evt.time || new Date().toISOString(),
            data: evt // The full event object
        });
        setActiveCard('ledger');

        // 2. Selection Logic
        const newSet = new Set(isMulti ? selectedEventIds : []);
        if (newSet.has(evt.id)) {
            newSet.delete(evt.id);
        } else {
            newSet.add(evt.id);
        }
        setSelectedEventIds(newSet);
    };

    // Helper Component for Card Actions
    const CardAction = ({ title, data }: { title: string, data: any }) => {
        const sourceId = getSourceId(title);
        const isAdded = items.some(i => i.sourceId === sourceId);

        return (
            <button
                onClick={(e) => { e.stopPropagation(); handleToggleContext(title, data); }}
                className={`absolute top-3 right-3 transition-all p-1.5 rounded-full ${isAdded ? 'opacity-100 bg-red-500/10 text-red-500' : 'opacity-0 group-hover:opacity-100 hover:bg-white/10 text-blue-400'}`}
                title={isAdded ? "Remove from Context" : "Add to Context"}
            >
                {isAdded ? <div className="w-4 h-4 flex items-center justify-center font-bold text-lg leading-none">-</div> : <PlusCircle size={16} />}
            </button>
        );
    };

    const fullContextId = 'power-full-state';
    const isFullContextAdded = items.some(i => i.sourceId === fullContextId);

    return (
        <div className="h-full w-full flex flex-col overflow-hidden bg-bg-app">
            {/* Header */}
            <div className="px-6 py-4 border-b border-subtle flex items-center justify-between bg-surface-1">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-yellow-500/10 rounded-lg">
                        <Zap className="w-5 h-5 text-yellow-500" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-primary">Power State Machine</h1>
                        <p className="text-xs text-secondary">Forensic Transition Ledger • Invariants • Wake Sources</p>
                    </div>
                </div>
                {/* Global Context Button */}
                <button
                    onClick={() => {
                        if (isFullContextAdded) removeItemsBySourceId([fullContextId]);
                        else addItem({ type: 'evidence', title: "Full Power State", description: "Complete Power Telemetry Snapshot", data: powerData, evidenceClass: 'derived', rank: 5, sourceId: fullContextId });
                    }}
                    className={`flex items-center gap-2 px-3 py-1.5 border rounded-md text-xs font-bold transition-colors ${isFullContextAdded
                        ? 'bg-red-500/10 border-red-500/30 text-red-500 hover:bg-red-500/20'
                        : 'bg-surface-2 hover:bg-surface-3 border-subtle text-secondary'
                        }`}
                >
                    {isFullContextAdded ? <div className="w-3.5 h-3.5 flex items-center justify-center font-bold">-</div> : <PlusCircle size={14} />}
                    <span>{isFullContextAdded ? 'Remove Full Context' : 'Add Full Context'}</span>
                </button>
            </div>

            <div className="p-6 overflow-y-auto custom-scrollbar space-y-6">

                {/* Top Row: State & Invariants */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">

                    {/* Platform State Card */}
                    <div
                        className={`group relative bg-surface-1 border rounded-xl p-4 flex flex-col justify-between transition-colors cursor-pointer ${activeCard === 'platform' ? 'border-blue-500 bg-blue-500/5' : 'border-subtle hover:border-blue-500/30'}`}
                        onClick={() => {
                            setActiveCard('platform');
                            setInspectableItem({
                                type: 'state',
                                panelTitle: 'Platform State',
                                title: 'Platform Power Configuration',
                                description: 'Critical platform power invariants determining sleep capabilities and power source.',
                                LevelDisplayName: 'Information',
                                ProviderName: 'PowerConfig',
                                TimeCreated: new Date().toISOString(),
                                data: invariants // Pass the rich data
                            });
                        }}
                    >
                        <CardAction title="Platform State" data={{ sleep_model: invariants.sleep_model, power_source: invariants.power_source }} />
                        <div>
                            <div className="flex items-center gap-2 mb-3 text-primary font-bold">
                                <Activity className="w-4 h-4 text-blue-400" />
                                <span>Platform State</span>
                            </div>
                            <div className="space-y-3">
                                <div>
                                    <div className="text-[10px] text-secondary uppercase tracking-wider mb-1">Sleep Model</div>
                                    <div className="text-sm font-mono text-primary flex items-center gap-2">
                                        {invariants.sleep_model?.value}
                                        {invariants.sleep_model?.value?.includes("S0ix") && (
                                            <span className="text-[10px] bg-blue-500/20 text-blue-400 px-1.5 rounded">MODERN</span>
                                        )}
                                    </div>
                                    <div className="text-[10px] text-tertiary opacity-50">Source: {invariants.sleep_model?.source}</div>
                                </div>
                                <div className="h-px bg-white/5" />
                                <div>
                                    <div className="text-[10px] text-secondary uppercase tracking-wider mb-1">Power Source</div>
                                    <div className={`text-sm font-mono flex items-center gap-2 ${invariants.power_source?.value?.includes("AC") ? 'text-green-400' : 'text-yellow-400'}`}>
                                        {invariants.power_source?.value}
                                    </div>
                                    <div className="text-[10px] text-tertiary opacity-50">Source: {invariants.power_source?.source}</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* ASPM Policy Card */}
                    <div
                        className={`group relative bg-surface-1 border rounded-xl p-4 transition-colors cursor-pointer ${activeCard === 'aspm' ? 'border-blue-500 bg-blue-500/5' : 'border-subtle hover:border-blue-500/30'}`}
                        onClick={() => {
                            setActiveCard('aspm');
                            setInspectableItem({
                                type: 'policy',
                                panelTitle: 'ASPM Policy',
                                title: 'PCIe ASPM Settings',
                                description: 'Active State Power Management (ASPM) settings for AC and DC power profiles.',
                                LevelDisplayName: 'Information',
                                ProviderName: 'PowerConfig (ASPM)',
                                TimeCreated: new Date().toISOString(),
                                data: invariants.pcie_aspm
                            });
                        }}
                    >
                        <CardAction title="ASPM Policy" data={invariants.pcie_aspm} />
                        <div className="flex items-center gap-2 mb-3 text-primary font-bold">
                            <BatteryCharging className="w-4 h-4 text-green-500" />
                            <span>PCIe Power (ASPM)</span>
                        </div>
                        <div className="grid grid-cols-2 gap-2 mb-3">
                            <div className="p-2 bg-surface-2 rounded border border-subtle">
                                <div className="text-[10px] text-secondary uppercase tracking-wider mb-1">AC (Plugged In)</div>
                                <div className="font-mono text-primary">{invariants.pcie_aspm?.decoded_ac}</div>
                                <div className="text-[10px] text-tertiary opacity-50">{invariants.pcie_aspm?.ac_index}</div>
                            </div>
                            <div className="p-2 bg-surface-2 rounded border border-subtle">
                                <div className="text-[10px] text-secondary uppercase tracking-wider mb-1">DC (Battery)</div>
                                <div className="font-mono text-secondary">{invariants.pcie_aspm?.decoded_dc}</div>
                                <div className="text-[10px] text-tertiary opacity-50">{invariants.pcie_aspm?.dc_index}</div>
                            </div>
                        </div>
                        <div className="flex items-center gap-2 text-[10px] text-secondary bg-surface-2 p-2 rounded">
                            <Info className="w-3 h-3 text-tertiary" />
                            <span>Source: {invariants.pcie_aspm?.source}</span>
                        </div>
                    </div>

                    {/* Wake Armed & Fast Startup */}
                    <div
                        className={`group relative bg-surface-1 border rounded-xl p-4 transition-colors cursor-pointer ${activeCard === 'wake' ? 'border-blue-500 bg-blue-500/5' : 'border-subtle hover:border-blue-500/30'}`}
                        onClick={() => {
                            setActiveCard('wake');
                            setInspectableItem({
                                type: 'signal',
                                panelTitle: 'Wake Sources',
                                title: 'Wake-Armed Devices',
                                description: `List of ${signals?.wake_armed_count} devices currently configured to wake the system from sleep state.`,
                                LevelDisplayName: 'Information',
                                ProviderName: 'PowerConfig (Wake)',
                                TimeCreated: new Date().toISOString(),
                                data: signals // Includes wake_armed array
                            });
                        }}
                    >
                        <CardAction title="Wake Sources" data={signals.wake_armed} />
                        <div className="flex items-center gap-2 mb-3 text-primary font-bold">
                            <Bell className="w-4 h-4 text-orange-400" />
                            <span>Wake Sources ({signals?.wake_armed_count || 0})</span>
                        </div>
                        <div className="h-[120px] overflow-y-auto custom-scrollbar bg-surface-2 rounded border border-subtle p-2 text-xs font-mono text-secondary">
                            {signals?.wake_armed?.length > 0 ? (
                                <ul className="space-y-1">
                                    {signals.wake_armed.map((dev: string, i: number) => (
                                        <li key={i} className="truncate">• {dev}</li>
                                    ))}
                                </ul>
                            ) : (
                                <div className="text-tertiary italic">No devices armed for wake.</div>
                            )}
                        </div>
                    </div>
                </div>

                {/* Forensic Ledger */}
                <div>
                    <div className="flex items-center justify-between mb-2">
                        <h3 className="text-sm font-bold text-secondary flex items-center gap-2">
                            <Clock className="w-4 h-4" />
                            Transition Ledger (Last {transitions?.window_minutes} min)
                        </h3>
                        <div className="flex items-center gap-2">
                            {/* Context Actions for Ledger */}
                            {derived?.has_gpu_reset && (
                                <span className="text-xs bg-red-500/10 text-red-500 px-2 py-0.5 rounded border border-red-500/20 font-bold">
                                    GPU RESET DETECTED
                                </span>
                            )}
                            {selectedEventIds.size > 0 && (
                                <button
                                    onClick={handleLedgerSelectionAdd}
                                    className="flex items-center gap-1.5 px-2 py-1 bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-blue-500/20 rounded text-[10px] font-bold uppercase tracking-wide transition-colors"
                                >
                                    <ListChecks size={12} />
                                    <span>Add {selectedEventIds.size} Events</span>
                                </button>
                            )}
                            <button
                                onClick={handleAddAllLedger}
                                className={`flex items-center gap-1.5 px-2 py-1 border rounded text-[10px] font-bold uppercase tracking-wide transition-colors ${items.some(i => i.sourceId === 'power-ledger-full')
                                    ? 'bg-red-500/10 text-red-500 border-red-500/30 hover:bg-red-500/20'
                                    : 'bg-surface-2 hover:bg-surface-3 text-secondary border-subtle'
                                    }`}
                            >
                                {items.some(i => i.sourceId === 'power-ledger-full') ?
                                    <div className="w-3 h-3 flex items-center justify-center font-bold">-</div> :
                                    <PlusCircle size={12} />
                                }
                                <span>{items.some(i => i.sourceId === 'power-ledger-full') ? 'Remove' : 'Add All'}</span>
                            </button>
                        </div>
                    </div>

                    <div className="bg-surface-1 border border-subtle rounded-xl overflow-hidden">
                        <table className="w-full text-left text-xs">
                            <thead className="bg-surface-2 text-tertiary uppercase tracking-wider font-semibold">
                                <tr>
                                    <th className="px-4 py-2 w-8">
                                        <div className="w-3 h-3 rounded-full border border-subtle" />
                                    </th>
                                    <th className="px-4 py-2 w-32">Time</th>
                                    <th className="px-4 py-2 w-24">Type</th>
                                    <th className="px-4 py-2 w-48">Provider</th>
                                    <th className="px-4 py-2">Details</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-white/5">
                                {ledger.length > 0 ? (
                                    ledger.map((evt: any, i: number) => {
                                        const isSelected = selectedEventIds.has(evt.id);
                                        return (
                                            <tr
                                                key={i}
                                                className={`transition-colors cursor-pointer group ${isSelected ? 'bg-blue-500/10 hover:bg-blue-500/20' : 'hover:bg-white/5'}`}
                                                onClick={(e) => handleEventClick(evt, e.ctrlKey || e.metaKey)}
                                            >
                                                <td className="px-4 py-2">
                                                    <div className={`w-3 h-3 rounded-full border flex items-center justify-center transition-colors ${isSelected ? 'bg-blue-500 border-blue-500' : 'border-subtle group-hover:border-secondary'}`}>
                                                        {isSelected && <Check size={8} className="text-white" />}
                                                    </div>
                                                </td>
                                                <td className="px-4 py-2 font-mono text-secondary">
                                                    {new Date(evt.time).toLocaleTimeString()}
                                                </td>
                                                <td className="px-4 py-2 font-bold">
                                                    <span className={`
                                                        ${evt.type === 'Sleep' ? 'text-blue-400' : ''}
                                                        ${evt.type === 'Wake' ? 'text-green-400' : ''}
                                                        ${evt.type.includes('Reset') || evt.type.includes('Shutdown') ? 'text-red-400' : 'text-primary'}
                                                    `}>
                                                        {evt.type}
                                                    </span>
                                                </td>
                                                <td className="px-4 py-2 text-tertiary truncate max-w-[150px]" title={evt.provider}>
                                                    {evt.provider}
                                                </td>
                                                <td className="px-4 py-2 text-secondary truncate max-w-[300px]" title={evt.message || ''}>
                                                    {evt.message || '-'}
                                                </td>
                                            </tr>
                                        );
                                    })
                                ) : (
                                    <tr>
                                        <td colSpan={5} className="px-4 py-8 text-center text-tertiary italic">
                                            No power transitions recorded in the last hour.
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>

            </div>
        </div>
    );
};
