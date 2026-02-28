import React, { useState } from 'react';
import { Database, AlertTriangle, HardDrive, Disc, Thermometer, Activity, PlusCircle, Server, FileText } from 'lucide-react';
import { useSystemStore } from '@/lib/store';
import { useContextStore } from '../../../context/ContextStore';

export const StorageView: React.FC = () => {
    const report = useSystemStore(state => state.diagnosticReport);
    const { setInspectableItem } = useSystemStore();
    const { addItem, items, removeItemsBySourceId } = useContextStore();

    // UI State
    const [activeCard, setActiveCard] = useState<string | null>(null);

    // Unpack data
    const storage = report?.domains?.hardware?.details?.storage;
    const drives = storage?.drives || [];
    const risks = storage?.risks || [];

    if (!report || !storage) {
        return (
            <div className="p-12 flex flex-col items-center justify-center text-secondary h-full">
                <Database className="w-12 h-12 mb-4 opacity-20" />
                <h2 className="text-xl font-bold mb-2">Storage Forensics Unavailable</h2>
                <p>Telemetry required. Run Deep Scan.</p>
            </div>
        );
    }

    // --- Interaction Helpers ---

    const handleTileClick = (cardId: string, title: string, data: any) => {
        setActiveCard(cardId);
        setInspectableItem({
            uniqueId: `storage-${cardId}`,
            type: 'diagnostic',
            panelTitle: 'Storage Forensics',
            title: title,
            description: `Forensic details for ${title}`,
            LevelDisplayName: 'Information',
            ProviderName: 'StorageHealth',
            TimeCreated: new Date().toISOString(),
            data: data
        });
    };

    const handleToggleContext = (title: string, data: any, id: string) => {
        const sourceId = `storage-${id}`;
        const exists = items.some((i: any) => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: title,
                description: `Storage Forensic Snapshot: ${data.identity?.model}`,
                data: data,
                evidenceClass: 'invariant',
                rank: 3,
                sourceId: sourceId
            });
        }
    };

    const handleFullContext = () => {
        const sourceId = 'storage-full-pack';
        const exists = items.some((i: any) => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: 'Full Storage Forensics',
                description: `Complete inventory of ${drives.length} physical drives and health metrics.`,
                data: storage,
                evidenceClass: 'invariant',
                rank: 2,
                sourceId: sourceId
            });
        }
    };

    const isFullContextAdded = items.some((i: any) => i.sourceId === 'storage-full-pack');

    return (
        <div className="flex flex-col h-full w-full">
            {/* Header */}
            <header className="h-16 flex items-center justify-between px-6 shrink-0 border-b border-subtle">
                <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-lg bg-orange-500/10 flex items-center justify-center border border-orange-500/20">
                        <Database className="w-5 h-5 text-orange-400" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-primary flex items-center gap-2">
                            Storage Forensics
                            <span className="text-secondary font-normal text-sm px-2 py-0.5 rounded bg-surface-2 border border-white/5 font-mono">
                                {drives.length} DISKS
                            </span>
                        </h1>
                        <div className="flex items-center gap-3 text-xs text-secondary">
                            <span className="font-mono">SMART INTERROGATION ACTIVE</span>
                        </div>
                    </div>
                </div>

                <div className="flex items-center gap-3">
                    {risks.length > 0 && (
                        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-red-500/10 border border-red-500/20 text-red-500 text-xs font-bold animate-pulse">
                            <AlertTriangle className="w-3.5 h-3.5" />
                            <span>{risks.length} HEALTH RISKS</span>
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
                        {isFullContextAdded ? 'Remove Context' : '+ Export Storage Model'}
                    </button>
                </div>
            </header>

            <div className="p-6 overflow-y-auto custom-scrollbar flex-1 flex flex-col gap-6">

                {drives.map((disk: any, idx: number) => {
                    const isHealthy = disk.health?.status === 'Healthy';
                    const isNVMe = disk.identity?.bus_type === 'NVMe';
                    const active = activeCard === disk.id;
                    const contextAdded = items.some((i: any) => i.sourceId === `storage-${disk.id}`);

                    return (
                        <div
                            key={disk.id}
                            onClick={() => handleTileClick(disk.id, disk.identity?.model, disk)}
                            className={`group relative p-6 rounded-xl border transition-all cursor-pointer flex flex-col gap-4
                                ${active
                                    ? 'bg-orange-500/5 border-orange-500/40 shadow-[0_0_15px_rgba(249,115,22,0.1)]'
                                    : 'bg-surface-1 border-subtle hover:border-primary/30'
                                }`}
                        >
                            {/* Context Action */}
                            <button
                                onClick={(e) => { e.stopPropagation(); handleToggleContext(disk.identity?.model, disk, disk.id); }}
                                className={`absolute top-4 right-4 transition-all p-1.5 rounded-full z-10 
                                    ${contextAdded
                                        ? 'opacity-100 bg-red-500/10 text-red-500'
                                        : 'opacity-0 group-hover:opacity-100 hover:bg-surface-3 text-orange-400'
                                    }`}
                            >
                                {contextAdded ? <PlusCircle className="w-4 h-4 rotate-45" /> : <PlusCircle className="w-4 h-4" />}
                            </button>

                            {/* Top Row: Identity & Primary Status */}
                            <div className="flex flex-col lg:flex-row gap-6 justify-between items-start">
                                <div className="flex items-center gap-4">
                                    <div className={`w-12 h-12 rounded-full flex items-center justify-center border 
                                        ${isHealthy ? 'bg-surface-2 border-white/5' : 'bg-red-500/10 border-red-500/30'}`}>
                                        {isNVMe
                                            ? <Activity className={`w-6 h-6 ${isHealthy ? 'text-orange-400' : 'text-red-500'}`} />
                                            : <HardDrive className={`w-6 h-6 ${isHealthy ? 'text-secondary' : 'text-red-500'}`} />
                                        }
                                    </div>
                                    <div>
                                        <div className="flex items-center gap-2 mb-1">
                                            <h3 className="text-lg font-bold text-primary">{disk.identity?.model}</h3>
                                            <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded border border-white/10 bg-surface-2 text-secondary">
                                                {disk.identity?.bus_type}
                                            </span>
                                            {!isHealthy && (
                                                <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded border border-red-500/30 bg-red-500/10 text-red-500">
                                                    {disk.health?.status || 'WARNING'}
                                                </span>
                                            )}
                                        </div>
                                        <div className="flex items-center gap-4 text-xs text-secondary font-mono">
                                            <span>FW: {disk.identity?.firmware}</span>
                                            <span className="hidden lg:inline">SN: {disk.identity?.serial || 'N/A'}</span>
                                            <span>{disk.capacity?.total_gb} GB</span>
                                        </div>
                                    </div>
                                </div>

                                {/* Health Metrics */}
                                <div className="flex gap-6 w-full lg:w-auto mt-4 lg:mt-0">
                                    {/* Wear Level */}
                                    <div className="flex flex-col items-center min-w-[80px]">
                                        <span className="text-[10px] uppercase text-tertiary mb-1">Wear</span>
                                        <div className="text-xl font-bold font-mono text-primary">
                                            {disk.health?.wear_percent !== null ? `${disk.health.wear_percent}%` : 'N/A'}
                                        </div>
                                    </div>

                                    {/* Temperature */}
                                    <div className="flex flex-col items-center min-w-[80px]">
                                        <span className="text-[10px] uppercase text-tertiary mb-1">Temp</span>
                                        <div className={`text-xl font-bold font-mono flex items-center gap-1
                                            ${(disk.health?.temperature_c || 0) > 70 ? 'text-red-400' : 'text-emerald-400'}`}>
                                            {disk.health?.temperature_c !== null ? `${disk.health.temperature_c}°C` : 'N/A'}
                                        </div>
                                    </div>

                                    {/* Errors */}
                                    {(disk.stats?.read_errors > 0 || disk.stats?.write_errors > 0) && (
                                        <div className="flex flex-col items-center min-w-[80px]">
                                            <span className="text-[10px] uppercase text-red-500 mb-1">Errors</span>
                                            <div className="text-xl font-bold font-mono text-red-400">
                                                {(disk.stats?.read_errors || 0) + (disk.stats?.write_errors || 0)}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Volumes / Partitions */}
                            <div className="mt-2 space-y-2">
                                {disk.volumes?.map((vol: any) => (
                                    <div key={vol.letter} className="bg-surface-2/30 rounded p-2 flex items-center gap-3 text-xs border border-white/5 hover:bg-surface-2/50 transition-colors">
                                        <div className="w-8 h-8 rounded bg-surface-3 flex items-center justify-center font-bold text-secondary border border-white/5">
                                            {vol.letter}:
                                        </div>
                                        <div className="flex-1">
                                            <div className="flex justify-between items-end mb-1">
                                                <span className="font-medium text-primary">{vol.label || 'Local Disk'}</span>
                                                <span className="text-secondary">{vol.free_gb} GB Free / {vol.size_gb} GB</span>
                                            </div>
                                            <div className="h-1.5 w-full bg-surface-3 rounded-full overflow-hidden">
                                                <div
                                                    className={`h-full rounded-full ${vol.used_percent > 90 ? 'bg-red-500' :
                                                        vol.used_percent > 75 ? 'bg-orange-400' : 'bg-blue-500'
                                                        }`}
                                                    style={{ width: `${vol.used_percent}%` }}
                                                />
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>

                        </div>
                    );
                })}

            </div>

            {/* Bottom Ledger: Storage Event Log */}
            <div className={`mx-6 mb-6 flex-1 bg-surface-1 rounded-xl border transition-all flex flex-col overflow-hidden min-h-[300px] shrink-0
                        ${activeCard === 'ledger' ? 'border-orange-500/40' : 'border-subtle'}
                    `}>
                <div className="p-4 border-b border-subtle flex justify-between items-center bg-surface-2/30">
                    <div className="flex items-center gap-2">
                        <FileText size={16} className="text-secondary" />
                        <h3 className="text-xs font-bold text-secondary uppercase tracking-wider">Storage Forensic Ledger</h3>
                    </div>
                    <div className="flex gap-2">
                        <span className="text-[10px] bg-surface-3 px-2 py-0.5 rounded text-secondary border border-white/5">Auto-Filtered: Disk, NTFS, NVMe</span>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto custom-scrollbar p-0">
                    {/* Table Header */}
                    <div className="grid grid-cols-12 gap-4 px-4 py-2 border-b border-subtle bg-surface-1 text-[10px] font-bold text-tertiary uppercase tracking-wider sticky top-0 z-10">
                        <div className="col-span-2">Time</div>
                        <div className="col-span-1">Level</div>
                        <div className="col-span-2">Provider</div>
                        <div className="col-span-7">Event Message</div>
                    </div>

                    {(() => {
                        const events = useSystemStore.getState().events || [];

                        const storageEvents = events.filter((e: any) => {
                            const p = (e.ProviderName || '').toLowerCase();
                            // Filter for Storage Providers + WHEA
                            // WHEA events usually have 'WHEA-Logger' provider
                            if (p.includes('whea')) return true;

                            // Storage stack providers
                            return ['disk', 'ntfs', 'stornvme', 'volsnap', 'partmgr', 'iastora', 'wer-systemErrorReporting'].some(k => p.includes(k));
                        }).sort((a: any, b: any) => new Date(b.TimeCreated).getTime() - new Date(a.TimeCreated).getTime());

                        if (storageEvents.length === 0) {
                            return (
                                <div className="flex flex-col items-center justify-center h-48 text-secondary/40">
                                    <Activity className="mb-2 opacity-50 w-8 h-8" />
                                    <span className="text-sm">No Storage Events Detected</span>
                                    <span className="text-xs opacity-70">NTFS, Disk, and Controller logs are clean.</span>
                                </div>
                            );
                        }

                        return (
                            <div className="divide-y divide-subtle">
                                {storageEvents.map((event: any) => (
                                    <div
                                        key={event.uniqueId || `evt-${event.Id}-${event.TimeCreated}`}
                                        onClick={() => {
                                            setActiveCard('ledger');
                                            setInspectableItem({
                                                uniqueId: event.uniqueId,
                                                type: 'event',
                                                title: `Storage Event: ${event.ProviderName}`,
                                                description: event.Message,
                                                data: event
                                            });
                                        }}
                                        className="grid grid-cols-12 gap-4 px-4 py-3 hover:bg-white/5 transition-colors cursor-pointer group text-xs"
                                    >
                                        <div className="col-span-2 font-mono text-secondary opacity-70">
                                            {new Date(event.TimeCreated).toLocaleTimeString()}
                                        </div>
                                        <div className="col-span-1">
                                            {event.LevelDisplayName === 'Error' || event.LevelDisplayName === 'Critical'
                                                ? <span className="font-bold text-red-400">ERR</span>
                                                : event.Level === 3 || event.LevelDisplayName === 'Warning'
                                                    ? <span className="font-bold text-orange-400">WARN</span>
                                                    : <span className="text-secondary">INFO</span>
                                            }
                                        </div>
                                        <div className="col-span-2 text-secondary overflow-hidden truncate" title={event.ProviderName}>
                                            {event.ProviderName}
                                        </div>
                                        <div className="col-span-7 text-primary group-hover:text-white transition-colors truncate">
                                            {event.Message}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        );
                    })()}
                </div>
            </div>

        </div>
    );
};
