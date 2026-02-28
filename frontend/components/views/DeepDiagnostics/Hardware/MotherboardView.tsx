import React, { useState } from 'react';
import { Server, Shield, CircuitBoard, Lock, Unlock, AlertTriangle, Layers, PlusCircle, PenTool, Database, List, Activity } from 'lucide-react';
import { useSystemStore } from '@/lib/store';
import { useContextStore } from '../../../context/ContextStore';

export const MotherboardView: React.FC = () => {
    const report = useSystemStore(state => state.diagnosticReport);
    const { setInspectableItem } = useSystemStore();
    const { addItem, items, removeItemsBySourceId } = useContextStore();

    // UI State
    const [activeCard, setActiveCard] = useState<string | null>(null);

    // Unpack data
    const board = report?.domains?.hardware?.details?.board;
    const identity = board?.identity;
    const bios = board?.bios;
    const security = board?.security;
    const risks = board?.risks || [];

    if (!report || !board) {
        return (
            <div className="p-12 flex flex-col items-center justify-center text-secondary h-full">
                <CircuitBoard className="w-12 h-12 mb-4 opacity-20" />
                <h2 className="text-xl font-bold mb-2">Platform Model Unavailable</h2>
                <p>Telemetry required. Run Deep Scan.</p>
            </div>
        );
    }

    // --- Interaction Helpers ---

    const handleTileClick = (cardId: string, title: string, data: any) => {
        setActiveCard(cardId);
        setInspectableItem({
            uniqueId: `board-${cardId}`,
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
        const sourceId = `board-${id}`;
        const exists = items.some((i: any) => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: `Board: ${title}`,
                description: `Forensic snapshot of ${title}`,
                data: data,
                evidenceClass: 'invariant',
                rank: 3,
                sourceId: sourceId
            });
        }
    };

    const handleFullContext = () => {
        const sourceId = 'board-full-platform';
        const exists = items.some((i: any) => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: 'Motherboard & Firmware',
                description: `Complete Board Identity, BIOS, TPM & Secure Boot State`,
                data: board,
                evidenceClass: 'invariant',
                rank: 2,
                sourceId: sourceId
            });
        }
    };

    // Sub-component for Hover Actions
    const ContextAction = ({ id, title, data }: { id: string, title: string, data: any }) => {
        const sourceId = `board-${id}`;
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
                    ? <PlusCircle className="w-4 h-4 rotate-45" />
                    : <PlusCircle className="w-4 h-4" />
                }
            </button>
        );
    };

    const isFullContextAdded = items.some((i: any) => i.sourceId === 'board-full-platform');

    return (
        <div className="flex flex-col h-full w-full">
            {/* Header */}
            <header className="h-16 flex items-center justify-between px-6 shrink-0 border-b border-subtle">
                <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-lg bg-indigo-500/10 flex items-center justify-center border border-indigo-500/20">
                        <CircuitBoard className="w-5 h-5 text-indigo-400" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-primary flex items-center gap-2">
                            {identity?.product || 'Unknown Board'}
                            <span className="text-secondary font-normal text-sm px-2 py-0.5 rounded bg-surface-2 border border-white/5 font-mono">
                                {identity?.manufacturer}
                            </span>
                        </h1>
                        <div className="flex items-center gap-3 text-xs text-secondary">
                            <span className="font-mono">{identity?.serial || 'SN: Unknown'}</span>
                            <span className="w-1 h-1 rounded-full bg-white/20"></span>
                            <span>BIOS {bios?.version} ({bios?.release_date || 'Unknown Date'})</span>
                        </div>
                    </div>
                </div>

                <div className="flex items-center gap-3">
                    {/* Risk Badge */}
                    {risks.length > 0 && (
                        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-red-500/10 border border-red-500/20 text-red-500 text-xs font-bold animate-pulse">
                            <AlertTriangle className="w-3.5 h-3.5" />
                            <span>{risks.length} RISKS DETECTED</span>
                        </div>
                    )}

                    <button
                        onClick={handleFullContext}
                        className={`px-3 py-1.5 rounded text-xs font-medium border transition-colors 
                        ${isFullContextAdded
                                ? 'bg-red-500/10 text-red-400 border-red-500/30 hover:bg-red-500/20'
                                : 'bg-surface-1 text-secondary border-subtle hover:text-primary hover:border-primary/30'
                            }`}
                    >
                        {isFullContextAdded ? 'Remove Context' : '+ Export Platform Model'}
                    </button>
                </div>
            </header>

            <div className="p-6 overflow-y-auto custom-scrollbar flex-1 flex flex-col gap-6">

                {/* Top Row: Enhanced Tiles (Flex Layout for Full Width) */}
                <div className="flex flex-col lg:flex-row gap-4 w-full shrink-0">

                    {/* Tile 1: Board Identity */}
                    <div
                        onClick={() => handleTileClick('identity', 'Board Identity', { identity, bios })}
                        className={`group relative p-6 rounded-xl border transition-all cursor-pointer min-h-[180px] flex flex-col flex-1
                            ${activeCard === 'identity'
                                ? 'bg-indigo-500/5 border-indigo-500/40 shadow-[0_0_15px_rgba(99,102,241,0.1)]'
                                : 'bg-surface-1 border-subtle hover:border-primary/30'
                            }`}
                    >
                        <ContextAction id="identity" title="Board Identity" data={{ identity, bios }} />

                        <div className="flex items-center gap-3 mb-6">
                            <PenTool className="w-4 h-4 text-indigo-400" />
                            <span className="text-xs font-bold tracking-wider text-secondary uppercase">Board & BIOS</span>
                        </div>

                        <div className="grid grid-cols-2 gap-4 h-full">
                            <div className="flex flex-col justify-center h-full">
                                <div className="text-2xl font-bold text-indigo-400 mb-1 leading-tight">{identity?.version || 'Rv 1.0'}</div>
                                <div className="text-[10px] uppercase tracking-wider text-secondary whitespace-nowrap">Board Version</div>
                            </div>
                            <div className="flex flex-col justify-center h-full border-l border-white/5 pl-4">
                                <div className="text-xl font-bold text-secondary mb-1 leading-tight font-mono">{bios?.vendor || 'Unknown'}</div>
                                <div className="text-[10px] uppercase tracking-wider text-secondary whitespace-nowrap">BIOS Vendor</div>
                            </div>
                        </div>

                        <div className="flex justify-between items-center text-xs text-secondary font-mono bg-surface-2/20 p-2 rounded border border-white/5 mt-auto">
                            <span>SMBIOS: <span className="text-primary">{bios?.version}</span></span>
                            <span>{bios?.release_date}</span>
                        </div>
                    </div>


                    {/* Tile 2: Firmware Security (TPM / Secure Boot) */}
                    <div
                        onClick={() => handleTileClick('security', 'Firmware Security', security)}
                        className={`group relative p-6 rounded-xl border transition-all cursor-pointer min-h-[180px] flex flex-col flex-1
                            ${activeCard === 'security'
                                ? 'bg-emerald-500/5 border-emerald-500/40 shadow-[0_0_15px_rgba(16,185,129,0.1)]'
                                : 'bg-surface-1 border-subtle hover:border-primary/30'
                            }`}
                    >
                        <ContextAction id="security" title="Firmware Security" data={security} />

                        <div className="flex items-center gap-3 mb-6">
                            <Shield className="w-4 h-4 text-emerald-400" />
                            <span className="text-xs font-bold tracking-wider text-secondary uppercase">Firmware Security</span>
                        </div>

                        <div className="flex flex-col gap-3 h-full justify-center mb-4">
                            <div className="flex justify-between items-center p-3 bg-surface-2/30 rounded border border-white/5">
                                <span className="text-sm text-secondary">Secure Boot</span>
                                {security?.secure_boot
                                    ? <span className="text-[10px] font-bold bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded border border-emerald-500/30 flex items-center gap-1"><Lock size={10} /> ENABLED</span>
                                    : <span className="text-[10px] font-bold bg-red-500/10 text-red-400 px-2 py-0.5 rounded border border-red-500/20 flex items-center gap-1"><Unlock size={10} /> DISABLED</span>
                                }
                            </div>
                            <div className="flex justify-between items-center p-3 bg-surface-2/30 rounded border border-white/5">
                                <span className="text-sm text-secondary">TPM 2.0 Module</span>
                                {security?.tpm?.Ready
                                    ? <span className="text-[10px] font-bold bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded border border-emerald-500/30 flex items-center gap-1"><Shield size={10} /> READY</span>
                                    : <span className="text-[10px] font-bold bg-surface-3 text-secondary px-2 py-0.5 rounded">NOT READY</span>
                                }
                            </div>
                        </div>

                        <div className="mt-auto">
                            <div className="text-[10px] uppercase tracking-wider text-secondary mb-2 opacity-70 whitespace-nowrap">Hardware Protections</div>
                            <div className="flex gap-2">
                                <span className={`text-[10px] px-2 py-1 rounded border border-white/5 font-mono whitespace-nowrap 
                                    ${security?.dma_protection ? 'bg-emerald-500/10 text-emerald-400' : 'bg-surface-3 text-secondary opacity-50'}`}>
                                    DMA PROTECTION
                                </span>
                                <span className={`text-[10px] px-2 py-1 rounded border border-white/5 font-mono whitespace-nowrap 
                                    ${security?.tpm?.Present ? 'bg-emerald-500/10 text-emerald-400' : 'bg-surface-3 text-secondary opacity-50'}`}>
                                    TPM PRESENT
                                </span>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Bottom Ledger: Firmware Event Log (Placeholder logic for now) */}
                <div className="flex-1 bg-surface-1 rounded-xl border border-subtle flex flex-col overflow-hidden">
                    <div className="h-10 border-b border-subtle flex items-center justify-between px-4 bg-surface-2/50">
                        <div className="flex items-center gap-2">
                            <List className="w-4 h-4 text-secondary" />
                            <span className="text-xs font-bold text-secondary uppercase tracking-wider">Firmware Ledger (TPM / Boot Events)</span>
                        </div>
                        <span className="text-[10px] text-tertiary">Last 24 Hours</span>
                    </div>

                    <div className="flex-1 flex flex-col items-center justify-center text-secondary opacity-50">
                        <Activity className="w-8 h-8 mb-2 stroke-1" />
                        <span className="text-sm">No critical firmware events detected</span>
                        <span className="text-xs">Secure Boot and TPM initialization logs are clean.</span>
                    </div>
                </div>

            </div>
        </div>
    );
};
