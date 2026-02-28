import React, { useState } from 'react';
import { Server, Zap, Shield, Microchip, Layers, PlusCircle, AlertTriangle, Activity, List, Clock, CheckCircle } from 'lucide-react';
import { useSystemStore } from '@/lib/store';
import { useContextStore } from '../../../context/ContextStore';

export const CpuView: React.FC = () => {
    const report = useSystemStore(state => state.diagnosticReport);
    const { setInspectableItem } = useSystemStore();
    const { addItem, items, removeItemsBySourceId } = useContextStore();

    // UI State
    const [activeCard, setActiveCard] = useState<string | null>(null);

    // Unpack data
    const cpu = report?.domains?.hardware?.details?.cpu || report?.domains?.cpu || report?.cpu;

    // Derived sub-objects (safely access properties)
    const identity = cpu?.identity;
    const topology = cpu?.topology;
    const security = cpu?.virtualization;
    const risks = cpu?.risks;

    if (!report || !cpu) {
        return (
            <div className="p-12 flex flex-col items-center justify-center text-secondary h-full">
                <Server className="w-12 h-12 mb-4 opacity-20" />
                <h2 className="text-xl font-bold mb-2">Platform Model Unavailable</h2>
                <p>Telemetry required. Run Deep Scan.</p>
            </div>
        );
    }

    // --- Interaction Helpers ---

    const handleTileClick = (cardId: string, title: string, data: any) => {
        setActiveCard(cardId);
        setInspectableItem({
            uniqueId: `cpu-${cardId}`,
            type: 'diagnostic',
            panelTitle: 'Hardware Forensics',
            title: title,
            description: `Deep forensic details for ${title}`,
            LevelDisplayName: 'Information',
            ProviderName: 'PlatformModel',
            TimeCreated: new Date().toISOString(),
            data: data
        });
    };

    const handleToggleContext = (title: string, data: any, id: string) => {
        const sourceId = `cpu-${id}`;
        const exists = items.some((i: any) => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: `CPU: ${title}`,
                description: `Forensic snapshot of ${title}`,
                data: data,
                evidenceClass: 'invariant',
                rank: 3,
                sourceId: sourceId
            });
        }
    };

    const handleFullContext = () => {
        const sourceId = 'cpu-full-platform';
        const exists = items.some((i: any) => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: 'CPU Platform Model',
                description: `Complete Processor Identity, Topology & Security Enclaves`,
                data: cpu,
                evidenceClass: 'invariant',
                rank: 2,
                sourceId: sourceId
            });
        }
    };

    // Sub-component for Hover Actions
    const ContextAction = ({ id, title, data }: { id: string, title: string, data: any }) => {
        const sourceId = `cpu-${id}`;
        const isAdded = items.some((i: any) => i.sourceId === sourceId);

        return (
            <button
                onClick={(e) => { e.stopPropagation(); handleToggleContext(title, data, id); }}
                className={`absolute top-3 right-3 transition-all p-1.5 rounded-full z-10 
                    ${isAdded
                        ? 'opacity-100 bg-red-500/10 text-red-500'
                        : 'opacity-0 group-hover:opacity-100 hover:bg-surface-3 text-blue-400'
                    }`}
                title={isAdded ? "Remove from Context" : "Add to Context"}
            >
                {isAdded
                    ? <div className="w-4 h-4 flex items-center justify-center font-bold text-lg leading-none">-</div>
                    : <PlusCircle size={16} />
                }
            </button>
        );
    };

    // Main Context Button State
    const isFullContextAdded = items.some((i: any) => i.sourceId === 'cpu-full-platform');

    return (
        <div className="flex flex-col h-full w-full overflow-hidden bg-bg-app">
            {/* Header */}
            <header className="px-6 py-5 border-b border-subtle flex items-center justify-between bg-surface-1 shrink-0">
                <div className="flex items-center gap-4">
                    <div className="p-3 bg-blue-500/10 rounded-xl border border-blue-500/20 shadow-[0_0_20px_rgba(59,130,246,0.1)]">
                        <Server className="w-8 h-8 text-blue-500" />
                    </div>
                    <div>
                        <h1 className="text-xl font-bold text-primary tracking-tight">{identity?.name}</h1>
                        <div className="flex items-center gap-2 text-sm text-secondary">
                            <span className="font-mono text-xs bg-surface-3 px-1.5 py-0.5 rounded text-secondary border border-white/5">{identity?.id}</span>
                            <span>•</span>
                            <span>Stepping {identity?.stepping}</span>
                            <span>•</span>
                            <span>Microcode {identity?.microcode}</span>
                        </div>
                    </div>
                </div>

                <button
                    onClick={handleFullContext}
                    className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider transition-all border
                        ${isFullContextAdded
                            ? 'bg-red-500/10 border-red-500/20 text-red-400 hover:bg-red-500/20'
                            : 'bg-surface-2 border-white/5 text-secondary hover:text-primary hover:border-white/10'
                        }`}
                >
                    {isFullContextAdded ? 'Remove Context' : '+ Export Platform Model'}
                </button>
            </header>

            <div className="p-6 overflow-y-auto custom-scrollbar flex-1 flex flex-col gap-6">

                {/* Top Row: Enhanced Tiles (Flex Layout for Full Width) */}
                <div className="flex flex-col lg:flex-row gap-4 w-full shrink-0">

                    {/* Tile 1: Core Topology */}
                    <div
                        onClick={() => handleTileClick('topology', 'Core Topology', topology)}
                        className={`group relative p-6 rounded-xl border transition-all cursor-pointer min-h-[180px] flex flex-col flex-1
                            ${activeCard === 'topology'
                                ? 'bg-blue-500/5 border-blue-500/40 shadow-[0_0_15px_rgba(59,130,246,0.1)]'
                                : 'bg-surface-1 border-subtle hover:border-primary/30'
                            }`}
                    >
                        <ContextAction id="topology" title="Core Topology" data={topology} />

                        <div className="flex items-center gap-2 mb-6">
                            <Layers size={20} className={activeCard === 'topology' ? 'text-blue-400' : 'text-secondary'} />
                            <span className={`text-sm font-bold uppercase tracking-wider ${activeCard === 'topology' ? 'text-blue-100' : 'text-secondary'}`}>
                                Core Topology
                            </span>
                        </div>

                        <div className="grid grid-cols-2 gap-6 mb-4 flex-1">
                            <div className="flex flex-col items-center justify-center p-4 bg-surface-2/30 rounded-lg border border-white/5 h-full">
                                <div className="text-3xl font-bold text-primary mb-1">{topology?.cores}</div>
                                <div className="text-[10px] uppercase tracking-wider text-secondary whitespace-nowrap">Physical Cores</div>
                            </div>
                            <div className="flex flex-col items-center justify-center p-4 bg-surface-2/30 rounded-lg border border-white/5 h-full">
                                <div className="text-3xl font-bold text-blue-400 mb-1">{topology?.threads}</div>
                                <div className="text-[10px] uppercase tracking-wider text-secondary whitespace-nowrap">Logical Threads</div>
                            </div>
                        </div>

                        <div className="flex justify-end items-center text-xs text-secondary font-mono bg-surface-2/20 p-2 rounded border border-white/5 mt-auto gap-4">
                            <span>L2: <span className="text-primary">{topology?.l2_cache_kb ? `${Math.round(topology.l2_cache_kb / 1024)}MB` : 'N/A'}</span></span>
                            <span>L3: <span className="text-primary">{topology?.l3_cache_kb ? `${Math.round(topology.l3_cache_kb / 1024)}MB` : 'N/A'}</span></span>
                        </div>
                    </div>

                    {/* Tile 2: Security & Virtualization */}
                    <div
                        onClick={() => handleTileClick('security', 'Security Enclaves', security)}
                        className={`group relative p-6 rounded-xl border transition-all cursor-pointer min-h-[180px] flex flex-col flex-1
                            ${activeCard === 'security'
                                ? 'bg-green-500/5 border-green-500/40 shadow-[0_0_15px_rgba(34,197,94,0.1)]'
                                : 'bg-surface-1 border-subtle hover:border-primary/30'
                            }`}
                    >
                        <ContextAction id="security" title="Security Enclaves" data={security} />

                        <div className="flex items-center gap-2 mb-6">
                            <Shield size={20} className={activeCard === 'security' ? 'text-green-400' : 'text-secondary'} />
                            <span className={`text-sm font-bold uppercase tracking-wider ${activeCard === 'security' ? 'text-green-100' : 'text-secondary'}`}>
                                Security & Virt
                            </span>
                        </div>

                        <div className="space-y-3 mb-4">
                            <div className="flex justify-between items-center p-3 bg-surface-2/30 rounded border border-white/5">
                                <span className="text-sm text-secondary">VBS Enclave</span>
                                {security?.vbs?.Running
                                    ? <span className="text-[10px] font-bold bg-green-500/20 text-green-400 px-2 py-0.5 rounded border border-green-500/30 flex items-center gap-1"><CheckCircle size={10} /> SECURE</span>
                                    : <span className="text-[10px] font-bold bg-surface-3 text-secondary px-2 py-0.5 rounded">DISABLED</span>
                                }
                            </div>
                            <div className="flex justify-between items-center p-3 bg-surface-2/30 rounded border border-white/5">
                                <span className="text-sm text-secondary">Hypervisor</span>
                                {security?.hyperv?.Present
                                    ? <span className="text-[10px] font-bold bg-purple-500/20 text-purple-400 px-2 py-0.5 rounded border border-purple-500/30 flex items-center gap-1"><Activity size={10} /> ACTIVE</span>
                                    : <span className="text-[10px] font-bold bg-surface-3 text-secondary px-2 py-0.5 rounded">OFFLINE</span>
                                }
                            </div>
                        </div>

                        <div className="mt-auto">
                            <div className="text-[10px] uppercase tracking-wider text-secondary mb-2 opacity-70 whitespace-nowrap">Supported Capabilities</div>
                            <div className="flex gap-2">
                                <span className="text-[10px] px-2 py-1 rounded bg-surface-3 text-secondary border border-white/5 font-mono whitespace-nowrap">VT-x / AMD-V</span>
                                <span className="text-[10px] px-2 py-1 rounded bg-surface-3 text-secondary border border-white/5 font-mono whitespace-nowrap">SLAT / EPT</span>
                                <span className="text-[10px] px-2 py-1 rounded bg-surface-3 text-secondary border border-white/5 font-mono whitespace-nowrap">IOMMU</span>
                            </div>
                        </div>
                    </div>

                </div>

                {/* Bottom Row: Full Width Ledger */}
                <div
                    className={`flex-1 rounded-xl border transition-all flex flex-col overflow-hidden
                        ${activeCard === 'ledger'
                            ? 'bg-surface-1 border-primary/40' // Active styling if we clicked it
                            : 'bg-surface-1 border-subtle'
                        }`}
                >
                    <div className="p-4 border-b border-subtle flex justify-between items-center bg-surface-2/30">
                        <div className="flex items-center gap-2">
                            <List size={16} className="text-secondary" />
                            <h3 className="text-xs font-bold text-secondary uppercase tracking-wider">Processor Ledger & Events</h3>
                        </div>
                        <div className="flex gap-2">
                            <span className="text-[10px] bg-surface-3 px-2 py-0.5 rounded text-secondary border border-white/5">Last 24 Hours</span>
                        </div>
                    </div>

                    <div className="flex-1 overflow-y-auto custom-scrollbar p-0">
                        {/* Table Header */}
                        <div className="grid grid-cols-12 gap-4 px-4 py-2 border-b border-subtle bg-surface-1 text-[10px] font-bold text-tertiary uppercase tracking-wider">
                            <div className="col-span-2">Time</div>
                            <div className="col-span-2">Type</div>
                            <div className="col-span-2">Provider</div>
                            <div className="col-span-6">Details</div>
                        </div>

                        {risks && risks.length > 0 ? (
                            <div className="divide-y divide-subtle">
                                {risks.map((risk: any, i: number) => (
                                    <div key={i} className="grid grid-cols-12 gap-4 px-4 py-3 hover:bg-white/5 transition-colors cursor-pointer group">
                                        <div className="col-span-2 text-xs font-mono text-secondary flex items-center gap-2">
                                            <Clock size={12} className="opacity-50" />
                                            {new Date().toLocaleTimeString()}
                                        </div>
                                        <div className="col-span-2 flex items-center">
                                            <span className="text-[10px] font-bold bg-red-500/10 text-red-400 px-1.5 py-0.5 rounded border border-red-500/20">RISK</span>
                                        </div>
                                        <div className="col-span-2 text-xs text-secondary">System Sentinel</div>
                                        <div className="col-span-6 text-xs text-primary group-hover:text-white transition-colors">{risk.message}</div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <div className="flex flex-col items-center justify-center h-32 text-secondary/40">
                                <Activity className="mb-2 opacity-50" />
                                <span className="text-sm">No Processor Events Detected</span>
                                <span className="text-xs opacity-70">MCA and WHEA logs are clean.</span>
                            </div>
                        )}
                    </div>
                </div>

            </div>
        </div>
    );
};
