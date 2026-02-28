import React, { useState } from 'react';
import { Monitor, Zap, Server, Activity, AlertTriangle, PlusCircle, List, Clock, CheckCircle } from 'lucide-react';
import { useSystemStore } from '@/lib/store';
import { useContextStore } from '../../../context/ContextStore';

export const GpuView: React.FC = () => {
    const report = useSystemStore(state => state.diagnosticReport);
    const { setInspectableItem } = useSystemStore();
    const { addItem, items, removeItemsBySourceId } = useContextStore();

    // UI State
    const [activeCard, setActiveCard] = useState<string | null>(null);

    const gpuData = report?.domains?.hardware?.details?.gpu;

    if (!report || !gpuData) {
        return (
            <div className="p-12 flex flex-col items-center justify-center text-secondary h-full">
                <Monitor className="w-12 h-12 mb-4 opacity-20" />
                <h2 className="text-xl font-bold mb-2">Graphics Forensics Unavailable</h2>
                <p>Telemetry required. Run Deep Scan.</p>
            </div>
        );
    }

    const { identity, driver, stability, risks } = gpuData;

    // --- Interaction Helpers ---

    const handleTileClick = (cardId: string, title: string, data: any) => {
        setActiveCard(cardId);
        setInspectableItem({
            uniqueId: `gpu-${cardId}`,
            type: 'diagnostic',
            panelTitle: 'Graphics Forensics',
            title: title,
            description: `Deep forensic details for ${title}`,
            LevelDisplayName: 'Information',
            ProviderName: 'PlatformModel',
            TimeCreated: new Date().toISOString(),
            data: data
        });
    };

    const handleToggleContext = (title: string, data: any, id: string) => {
        const sourceId = `gpu-${id}`;
        const exists = items.some((i: any) => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: `GPU: ${title}`,
                description: `Forensic snapshot of ${title}`,
                data: data,
                evidenceClass: 'invariant',
                rank: 3,
                sourceId: sourceId
            });
        }
    };

    const handleFullContext = () => {
        const sourceId = 'gpu-data-full';
        const exists = items.some((i: any) => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: 'GPU Platform Model',
                description: `Complete Graphics Identity, Driver & Stability Context`,
                data: gpuData,
                evidenceClass: 'invariant',
                rank: 2,
                sourceId: sourceId
            });
        }
    };

    // Sub-component for Hover Actions
    const ContextAction = ({ id, title, data }: { id: string, title: string, data: any }) => {
        const sourceId = `gpu-${id}`;
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

    const isFullContextAdded = items.some((i: any) => i.sourceId === 'gpu-data-full');

    return (
        <div className="flex flex-col h-full w-full overflow-hidden bg-bg-app">
            {/* Header */}
            <header className="px-6 py-5 border-b border-subtle flex items-center justify-between bg-surface-1 shrink-0">
                <div className="flex items-center gap-4">
                    <div className="p-3 bg-green-500/10 rounded-xl border border-green-500/20 shadow-[0_0_20px_rgba(34,197,94,0.1)]">
                        <Monitor className="w-8 h-8 text-green-500" />
                    </div>
                    <div>
                        <h1 className="text-xl font-bold text-primary tracking-tight">{identity?.name}</h1>
                        <div className="flex items-center gap-2 text-sm text-secondary">
                            <span className="font-mono text-xs bg-surface-3 px-1.5 py-0.5 rounded text-secondary border border-white/5">{identity?.processor || 'Standard GPU'}</span>
                            <span>•</span>
                            <span>{identity?.vram_mb ? `${(identity.vram_mb / 1024).toFixed(1)} GB VRAM` : 'Shared Memory'}</span>
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

                {/* Top Row: Enhanced Tiles (3 Columns) */}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 shrink-0">

                    {/* Tile 1: Driver Provenance */}
                    <div
                        onClick={() => handleTileClick('driver', 'Driver Stack', driver)}
                        className={`group relative p-5 rounded-xl border transition-all cursor-pointer min-h-[180px] flex flex-col justify-between
                             ${activeCard === 'driver'
                                ? 'bg-purple-500/5 border-purple-500/40 shadow-[0_0_15px_rgba(168,85,247,0.1)]'
                                : 'bg-surface-1 border-subtle hover:border-primary/30'
                            }`}
                    >
                        <ContextAction id="driver" title="Driver Stack" data={driver} />

                        <div className="flex items-center gap-2 mb-4">
                            <Server size={18} className={activeCard === 'driver' ? 'text-purple-400' : 'text-secondary'} />
                            <span className={`text-sm font-bold uppercase tracking-wider ${activeCard === 'driver' ? 'text-purple-100' : 'text-secondary'}`}>
                                Driver Stack
                            </span>
                        </div>

                        <div className="space-y-3">
                            <div>
                                <div className="text-[10px] text-secondary uppercase tracking-wider mb-1 opacity-70">Driver Version</div>
                                <div className="text-lg font-mono text-primary truncate">{driver?.version}</div>
                            </div>
                            <div className="flex justify-between items-end">
                                <div>
                                    <div className="text-[10px] text-secondary uppercase tracking-wider mb-1 opacity-70">Release Date</div>
                                    <div className="text-sm text-primary">{driver?.date}</div>
                                </div>
                                <span className="text-[10px] px-2 py-0.5 rounded bg-green-500/10 text-green-500 border border-green-500/20 font-bold tracking-wide">
                                    SIGNED / DCH
                                </span>
                            </div>
                        </div>
                    </div>

                    {/* Tile 2: Stability Engine */}
                    <div
                        onClick={() => handleTileClick('stability', 'Stability Engine', stability)}
                        className={`group relative p-5 rounded-xl border transition-all cursor-pointer min-h-[180px] flex flex-col justify-between
                             ${activeCard === 'stability'
                                ? 'bg-orange-500/5 border-orange-500/40 shadow-[0_0_15px_rgba(249,115,22,0.1)]'
                                : 'bg-surface-1 border-subtle hover:border-primary/30'
                            }`}
                    >
                        <ContextAction id="stability" title="TDR Configuration" data={stability} />

                        <div className="flex items-center gap-2 mb-4">
                            <Activity size={18} className={activeCard === 'stability' ? 'text-orange-400' : 'text-secondary'} />
                            <span className={`text-sm font-bold uppercase tracking-wider ${activeCard === 'stability' ? 'text-orange-100' : 'text-secondary'}`}>
                                Stability Engine
                            </span>
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <div className="p-2 bg-surface-2/30 rounded border border-white/5">
                                <div className="text-[10px] text-secondary uppercase tracking-wider mb-1">TDR Delay</div>
                                <div className="font-mono text-sm text-primary">{stability?.tdr?.TdrDelay}</div>
                            </div>
                            <div className="p-2 bg-surface-2/30 rounded border border-white/5">
                                <div className="text-[10px] text-secondary uppercase tracking-wider mb-1">TDR Level</div>
                                <div className="font-mono text-sm text-primary">{stability?.tdr?.TdrLevel}</div>
                            </div>
                        </div>
                        <div className="mt-auto pt-2 text-xs text-secondary/70 italic leading-snug">
                            Controls driver recovery timeout logic.
                        </div>
                    </div>

                    {/* Tile 3: Active Mode */}
                    <div
                        onClick={() => handleTileClick('mode', 'Display Topology', identity)}
                        className={`group relative p-5 rounded-xl border transition-all cursor-pointer min-h-[180px] flex flex-col justify-between
                             ${activeCard === 'mode'
                                ? 'bg-cyan-500/5 border-cyan-500/40 shadow-[0_0_15px_rgba(6,182,212,0.1)]'
                                : 'bg-surface-1 border-subtle hover:border-primary/30'
                            }`}
                    >
                        <ContextAction id="mode" title="Display Topology" data={identity} />

                        <div className="flex items-center gap-2 mb-4">
                            <Zap size={18} className={activeCard === 'mode' ? 'text-cyan-400' : 'text-secondary'} />
                            <span className={`text-sm font-bold uppercase tracking-wider ${activeCard === 'mode' ? 'text-cyan-100' : 'text-secondary'}`}>
                                Active Topology
                            </span>
                        </div>

                        <div className="flex flex-col items-center justify-center flex-1 py-2">
                            <div className="text-2xl font-bold text-primary mb-1">
                                {identity?.mode?.split(' x ')?.[0] || 'Unknown'} x {identity?.mode?.split(' x ')?.[1] || ''}
                            </div>
                            <div className="flex gap-2 mt-2">
                                <span className="px-2 py-0.5 rounded bg-surface-3 text-xs font-mono text-secondary border border-white/5">
                                    {identity?.mode?.split('@ ')?.[1] || '60Hz'}
                                </span>
                                <span className="px-2 py-0.5 rounded bg-surface-3 text-xs font-mono text-secondary border border-white/5">
                                    32-bit Depth
                                </span>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Bottom Row: Full Width Ledger */}
                <div
                    className={`flex-1 rounded-xl border transition-all flex flex-col overflow-hidden
                        ${activeCard === 'ledger'
                            ? 'bg-surface-1 border-primary/40'
                            : 'bg-surface-1 border-subtle'
                        }`}
                >
                    <div className="p-4 border-b border-subtle flex justify-between items-center bg-surface-2/30">
                        <div className="flex items-center gap-2">
                            <List size={16} className="text-secondary" />
                            <h3 className="text-xs font-bold text-secondary uppercase tracking-wider">Graphics Ledger</h3>
                        </div>
                        <div className="flex gap-2">
                            <span className="text-[10px] bg-surface-3 px-2 py-0.5 rounded text-secondary border border-white/5 justify-between">
                                {risks?.length > 0 ? `${risks.length} Risks Detected` : 'Healthy System'}
                            </span>
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
                                <CheckCircle className="mb-2 opacity-50 text-green-500/50" />
                                <span className="text-sm">No Graphics Anomalies Detected</span>
                                <span className="text-xs opacity-70">Driver and Configuration signals are normative.</span>
                            </div>
                        )}
                    </div>
                </div>

            </div>
        </div>
    );
};
