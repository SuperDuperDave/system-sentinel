import React, { useMemo, useState } from 'react';
import { useSystemStore } from '@/lib/store';
import { useContextStore } from '../../context/ContextStore';
import { useSelectionStore } from '@/lib/selectionStore';
import { Grid, Cpu, AlertTriangle, CheckCircle, Info, Zap, Activity, Clock, PlusCircle } from 'lucide-react';
import { EventList } from '../../EventList';

const DimmSlot = ({ stick, index, isSelected, onSelect }: { stick: any, index: number, isSelected: boolean, onSelect: () => void }) => {
    const isEmpty = stick.status === 'Empty';
    const isEcc = stick.ecc_present;
    const isXmp = stick.speed_configured > stick.speed_rated;

    // Ghost styling for empty slots
    if (isEmpty) {
        return (
            <div className="relative p-3 bg-surface-1 border border-dashed border-subtle rounded-lg opacity-50 flex flex-col justify-center items-center min-h-[100px]">
                <div className="text-xs font-mono text-tertiary mb-1">{stick.device_locator || `Slot ${index + 1}`}</div>
                <div className="text-xs text-tertiary">EMPTY</div>
            </div>
        );
    }

    return (
        <div
            className={`relative group p-3 rounded-lg border transition-all cursor-pointer overflow-hidden ${isSelected
                ? 'bg-blue-500/10 border-blue-500 shadow-[0_0_15px_rgba(59,130,246,0.2)]'
                : 'bg-surface-2 border-subtle hover:border-blue-500/30'
                }`}
            onClick={(e) => {
                e.stopPropagation();
                onSelect();
            }}
        >
            <div className="flex justify-between items-start mb-2">
                <div className={`text-xs font-mono ${isSelected ? 'text-blue-300' : 'text-tertiary'}`}>{stick.device_locator}</div>
                {isEcc && <span className="text-[10px] bg-green-500/10 text-green-500 px-1 rounded">ECC</span>}
            </div>
            <div className="text-sm font-bold text-primary truncate mb-1">{stick.manufacturer}</div>
            <div className="text-xs text-secondary font-mono truncate mb-2">{stick.part_number}</div>
            <div className={`grid grid-cols-2 gap-2 text-[10px] border-t pt-2 ${isSelected ? 'border-blue-500/20' : 'border-white/5'}`}>
                <div>
                    <span className="text-tertiary">Speed</span>
                    <div className={`font-mono ${isXmp ? 'text-blue-400' : 'text-primary'}`}>{stick.speed_configured} MT/s</div>
                </div>
                <div>
                    <span className="text-tertiary">Volts</span>
                    <div className="font-mono text-primary">{stick.voltage_configured ? `${stick.voltage_configured}mV` : 'N/A'}</div>
                </div>
            </div>
        </div>
    );
};

export const MemoryView: React.FC = () => {
    const report = useSystemStore(state => state.diagnosticReport);
    const { setInspectableItem } = useSystemStore();
    const { addItem, items, removeItemsBySourceId } = useContextStore();
    const { clearSelection } = useSelectionStore();

    // Local visual state for component highlighting (mirroring PowerView pattern)
    const [activeSection, setActiveSection] = useState<string | null>(null);

    const memoryData = report?.domains?.memory || report?.memory;

    if (!report || !memoryData) {
        return (
            <div className="p-12 flex flex-col items-center justify-center text-secondary">
                <Grid className="w-12 h-12 mb-4 opacity-20" />
                <h2 className="text-xl font-bold mb-2">No Memory Context</h2>
                <p>Please run a Deep Scan from the Overview or Sidebar.</p>
            </div>
        );
    }

    // Unpack Quantum Signals
    const { topology, integrity, ledger } = memoryData;
    const sticks = topology?.sticks || [];
    const meta = topology?.meta || {};
    const events = ledger?.events || [];
    const stats = ledger?.stats || { whea_count: 0, bsod_count: 0 };

    // Split events for EventList
    // Backend sends Type='whea' or 'system'
    const { wheaLogs, sysLogs } = useMemo(() => {
        const w = [];
        const s = [];
        for (const e of events) {
            // Ensure ID exists for EventList keys
            if (!e.Id) e.Id = '0';
            if (e.Type === 'whea') w.push(e);
            else s.push(e);
        }
        return { wheaLogs: w, sysLogs: s };
    }, [events]);

    // --- Helpers ---

    const getSourceId = (key: string) => `memory-${key}`;

    const handleToggleContext = (title: string, data: any) => {
        const sourceId = getSourceId(title.toLowerCase().replace(/\s+/g, '-'));
        const exists = items.some(i => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: `Memory: ${title}`,
                description: `Memory Telemetry: ${title}`,
                data: data,
                evidenceClass: 'mixed',
                rank: 4,
                sourceId: sourceId
            });
        }
    };

    // --- Components ---

    const CardAction = ({ title, data }: { title: string, data: any }) => {
        const sourceId = getSourceId(title.toLowerCase().replace(/\s+/g, '-'));
        const isAdded = items.some(i => i.sourceId === sourceId);
        return (
            <button
                onClick={(e) => { e.stopPropagation(); handleToggleContext(title, data); }}
                className={`absolute top-3 right-3 transition-all p-1.5 rounded-full z-20 ${isAdded ? 'opacity-100 bg-red-500/10 text-red-500' : 'opacity-0 group-hover:opacity-100 hover:bg-white/10 text-blue-400'}`}
                title={isAdded ? "Remove" : "Add to Context"}
            >
                {isAdded ? <div className="w-4 h-4 flex items-center justify-center font-bold text-lg leading-none">-</div> : <PlusCircle size={16} />}
            </button>
        );
    };



    return (
        <div className="h-full w-full flex flex-col overflow-hidden bg-bg-app">
            {/* Header */}
            <div className="px-6 py-4 border-b border-subtle flex items-center justify-between bg-surface-1 shrink-0">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-purple-500/10 rounded-lg">
                        <Grid className="w-5 h-5 text-purple-500" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-primary">Memory & Stability</h1>
                        <p className="text-xs text-secondary">Physical Topology • Electrical Integrity • Forensic Ledger</p>
                    </div>
                </div>
                {/* Global Context */}
                <button
                    onClick={() => handleToggleContext("Full Memory State", memoryData)}
                    className={`flex items-center gap-2 px-3 py-1.5 border rounded-md text-xs font-bold transition-colors ${items.some(i => i.sourceId?.includes('memory-full'))
                        ? 'bg-red-500/10 border-red-500/30 text-red-500'
                        : 'bg-surface-2 hover:bg-surface-3 border-subtle text-secondary'
                        }`}
                >
                    {items.some(i => i.sourceId?.includes('memory-full')) ? 'Remove All' : 'Add Full Context'}
                </button>
            </div>

            <div className="p-6 overflow-y-auto custom-scrollbar space-y-6">

                {/* Top Grid: Topology & Integrity */}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

                    {/* Panel 1: Topology Map */}
                    <div className="lg:col-span-2 group relative bg-surface-1 border border-subtle rounded-xl p-5 flex flex-col">
                        <CardAction title="Memory Topology" data={topology} />
                        <div className="flex items-center gap-2 mb-4">
                            <Cpu className="w-4 h-4 text-blue-400" />
                            <h3 className="font-bold text-sm text-primary">Physical Topology</h3>
                            <span className="text-xs text-tertiary ml-2">
                                {meta.slots_used}/{meta.slots_total} Slots • Max {meta.max_capacity_gb ? `${Math.round(meta.max_capacity_gb)}GB` : 'Unknown'}
                            </span>
                        </div>

                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                            {sticks.map((stick: any, idx: number) => {
                                return (
                                    <DimmSlot
                                        key={idx}
                                        stick={stick}
                                        index={idx}
                                        isSelected={activeSection === `dimm-${idx}`}
                                        onSelect={() => {
                                            setActiveSection(`dimm-${idx}`);
                                            clearSelection();
                                            setInspectableItem({
                                                type: 'hardware',
                                                panelTitle: stick.device_locator || `DIMM ${idx}`,
                                                title: `${stick.manufacturer} ${stick.part_number}`,
                                                description: `Physical DIMM in ${stick.bank_label}`,
                                                data: stick,
                                                ProviderName: 'SMBIOS',
                                                TimeCreated: new Date().toISOString(),
                                                LevelDisplayName: 'Hardware'
                                            });
                                        }}
                                    />
                                );
                            })}
                        </div>
                    </div>

                    {/* Panel 2: Integrity & Signals */}
                    <div className="group relative bg-surface-1 border border-subtle rounded-xl p-5 flex flex-col justify-between">
                        <CardAction title="Kit Integrity" data={integrity} />
                        <div>
                            <div className="flex items-center gap-2 mb-4">
                                <Activity className="w-4 h-4 text-orange-400" />
                                <h3 className="font-bold text-sm text-primary">Signal Integrity</h3>
                            </div>

                            <div className="space-y-4">
                                {/* Kit Match */}
                                <div
                                    className={`flex items-center justify-between p-3 rounded-lg border transition-colors cursor-pointer ${activeSection === 'integrity-match'
                                        ? 'bg-blue-500/10 border-blue-500'
                                        : 'bg-surface-2 border-subtle hover:border-blue-500/30'
                                        }`}
                                    onClick={() => {
                                        setActiveSection('integrity-match');
                                        clearSelection();
                                        setInspectableItem({
                                            type: 'diagnostic',
                                            panelTitle: 'Kit Integrity',
                                            title: 'DIMM Kit Homogeneity',
                                            description: integrity?.score === 1.0 ? 'All DIMMs appear to be from the same matching kit.' : 'Mixed DIMM kits detected, which may cause instability.',
                                            data: integrity
                                        });
                                    }}
                                >
                                    <div>
                                        <div className="text-xs text-secondary mb-1">Kit Homogeneity</div>
                                        <div className={`text-sm font-bold ${integrity?.score === 1.0 ? 'text-green-400' : 'text-red-400'}`}>
                                            {integrity?.score === 1.0 ? 'MATCHED KITS' : 'MIXED KITS'}
                                        </div>
                                    </div>
                                    {integrity?.score === 1.0 ? <CheckCircle className="text-green-500" size={20} /> : <AlertTriangle className="text-red-500" size={20} />}
                                </div>

                                {/* ECC Status */}
                                <div
                                    className={`flex items-center justify-between p-3 rounded-lg border transition-colors cursor-pointer ${activeSection === 'integrity-ecc'
                                        ? 'bg-blue-500/10 border-blue-500'
                                        : 'bg-surface-2 border-subtle hover:border-blue-500/30'
                                        }`}
                                    onClick={() => {
                                        setActiveSection('integrity-ecc');
                                        clearSelection();
                                        setInspectableItem({
                                            type: 'diagnostic',
                                            panelTitle: 'ECC Status',
                                            title: 'Error Correction Code',
                                            description: 'Verifies if physical memory data path width supports parity/ECC bits.',
                                            data: {
                                                present: sticks.some((s: any) => s.ecc_present),
                                                details: sticks.map((s: any) => ({
                                                    slot: s.device_locator,
                                                    ecc: s.ecc_present,
                                                    widths: s.widths
                                                }))
                                            }
                                        });
                                    }}
                                >
                                    <div>
                                        <div className="text-xs text-secondary mb-1">Error Correction</div>
                                        <div className="text-sm font-bold text-primary">
                                            {sticks.some((s: any) => s.ecc_present) ? 'ECC ACTIVE' : 'NON-ECC'}
                                        </div>
                                    </div>
                                    <Zap className={sticks.some((s: any) => s.ecc_present) ? "text-green-500" : "text-tertiary"} size={20} />
                                </div>
                            </div>
                        </div>

                        {integrity?.issues?.length > 0 && (
                            <div className="mt-4 p-2 bg-red-500/10 border border-red-500/20 rounded text-xs text-red-300">
                                {integrity.issues[0]}
                            </div>
                        )}
                    </div>
                </div>

                {/* Bottom Panel: Forensic Ledger */}
                <div className="bg-surface-1 border border-subtle rounded-xl flex flex-col overflow-hidden min-h-[300px]">
                    <div className="p-4 border-b border-subtle flex items-center justify-between shrink-0">
                        <div className="flex items-center gap-3">
                            <Clock className="w-4 h-4 text-tertiary" />
                            <h3 className="font-bold text-sm text-primary">Forensic Stability Ledger (30 Days)</h3>
                            <div className="flex gap-2">
                                <span className="px-2 py-0.5 rounded bg-red-500/10 text-red-400 text-[10px] font-bold border border-red-500/20">
                                    {stats.bsod_count} CRASHES
                                </span>
                                <span className="px-2 py-0.5 rounded bg-yellow-500/10 text-yellow-500 text-[10px] font-bold border border-yellow-500/20">
                                    {stats.whea_count} WHEA ERRORS
                                </span>
                            </div>
                        </div>
                    </div>

                    {/* Ledger List via EventList */}
                    <EventList
                        systemEvents={sysLogs}
                        wheaEvents={wheaLogs}
                        filter="all"
                        selectedId={null}
                        onSelect={(evt) => {
                            if (!evt) return;
                            setInspectableItem({
                                type: evt.type === 'whea' ? 'whea' : 'diagnostic', // Explicitly mirror generic format
                                panelTitle: 'Stability Event',
                                title: evt.type === 'whea' ? 'Hardware Error' : 'System Event',
                                description: evt.Message,
                                LevelDisplayName: (evt as any).LevelDisplayName,
                                ProviderName: evt.ProviderName,
                                TimeCreated: evt.TimeCreated,
                                data: evt,
                                DecodedCPER: (evt as any).DecodedCPER // Pass CPER if present (future proof)
                            });
                        }}
                    />
                </div>
            </div>
        </div>
    );
};
