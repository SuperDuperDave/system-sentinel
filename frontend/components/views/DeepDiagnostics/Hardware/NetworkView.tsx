import React, { useState } from 'react';
import { Network, Activity, Wifi, Shield, PlusCircle, AlertTriangle, FileText, Globe } from 'lucide-react';
import { useSystemStore } from '@/lib/store';
import { useContextStore } from '../../../context/ContextStore';

export const NetworkView: React.FC = () => {
    const report = useSystemStore(state => state.diagnosticReport);
    const { setInspectableItem } = useSystemStore();
    const { addItem, items, removeItemsBySourceId } = useContextStore();

    // UI configuration
    const [activeCard, setActiveCard] = useState<string | null>(null);

    // Data Unpacking
    const network = report?.domains?.hardware?.details?.network;
    const adapters = network?.adapters || [];
    const risks = network?.risks || [];

    if (!report || !network) {
        return (
            <div className="p-12 flex flex-col items-center justify-center text-secondary h-full">
                <Network className="w-12 h-12 mb-4 opacity-20" />
                <h2 className="text-xl font-bold mb-2">Network Context Initializing...</h2>
                <p>Waiting for deep diagnostic telemetry.</p>
            </div>
        );
    }

    // --- Interaction Handlers ---

    const handleTileClick = (cardId: string, title: string, data: any) => {
        setActiveCard(cardId);
        setInspectableItem({
            uniqueId: `net-${cardId}`,
            type: 'diagnostic',
            panelTitle: 'Network Forensics',
            title: title,
            description: `Forensic details for Adapter: ${title}`,
            LevelDisplayName: 'Information',
            ProviderName: 'NetworkStack',
            TimeCreated: new Date().toISOString(),
            data: data
        });
    };

    const handleToggleContext = (title: string, data: any, id: string) => {
        const sourceId = `net-${id}`;
        const exists = items.some((i: any) => i.sourceId === sourceId);

        if (exists) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: title,
                description: `Network Context: ${data.identity?.description}`,
                data: data,
                evidenceClass: 'invariant',
                rank: 3,
                sourceId: sourceId
            });
        }
    };

    return (
        <div className="flex flex-col h-full w-full">
            {/* Header */}
            <header className="h-16 flex items-center justify-between px-6 shrink-0 border-b border-subtle">
                <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center border border-blue-500/20">
                        <Network className="w-5 h-5 text-blue-400" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-primary flex items-center gap-2">
                            Network Connectivity
                            <span className="text-secondary font-normal text-sm px-2 py-0.5 rounded bg-surface-2 border border-white/5 font-mono">
                                {adapters.length} ADAPTERS
                            </span>
                        </h1>
                        <div className="flex items-center gap-3 text-xs text-secondary">
                            <span className="font-mono">NDIS STACK INTERROGATED</span>
                        </div>
                    </div>
                </div>

                <div className="flex items-center gap-3">
                    {risks.length > 0 && (
                        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-red-500/10 border border-red-500/20 text-red-500 text-xs font-bold animate-pulse">
                            <AlertTriangle className="w-3.5 h-3.5" />
                            <span>{risks.length} CONFIG RISKS</span>
                        </div>
                    )}
                </div>
            </header>

            <div className="p-6 overflow-y-auto custom-scrollbar flex-1 flex flex-col gap-6">

                {/* Adapter Grid */}
                {adapters.map((adapter: any) => {
                    const isUp = adapter.status?.oper_status === 'Up';
                    const isWifi = adapter.status?.media_type === '802.3' ? false : true; // Simple heuristic, better to check NdisPhysicalMedium
                    const active = activeCard === adapter.id;
                    const contextAdded = items.some((i: any) => i.sourceId === `net-${adapter.id}`);

                    return (
                        <div
                            key={adapter.id}
                            onClick={() => handleTileClick(adapter.id, adapter.identity?.name, adapter)}
                            className={`group relative p-6 rounded-xl border transition-all cursor-pointer flex flex-col lg:flex-row gap-6 justify-between items-start
                                ${active
                                    ? 'bg-blue-500/5 border-blue-500/40 shadow-[0_0_15px_rgba(59,130,246,0.1)]'
                                    : 'bg-surface-1 border-subtle hover:border-primary/30'
                                }`}
                        >
                            {/* Context Action */}
                            <button
                                onClick={(e) => { e.stopPropagation(); handleToggleContext(adapter.identity?.name, adapter, adapter.id); }}
                                className={`absolute top-4 right-4 transition-all p-1.5 rounded-full z-10 
                                    ${contextAdded
                                        ? 'opacity-100 bg-blue-500/10 text-blue-500'
                                        : 'opacity-0 group-hover:opacity-100 hover:bg-surface-3 text-blue-400'
                                    }`}
                            >
                                {contextAdded ? <PlusCircle className="w-4 h-4 rotate-45" /> : <PlusCircle className="w-4 h-4" />}
                            </button>

                            {/* Left Column: Identity & Status */}
                            <div className="flex items-center gap-4">
                                <div className={`w-12 h-12 rounded-full flex items-center justify-center border 
                                    ${isUp ? 'bg-surface-2 border-green-500/30' : 'bg-surface-2 border-white/5 opacity-50'}`}>
                                    {isWifi
                                        ? <Wifi className={`w-6 h-6 ${isUp ? 'text-green-400' : 'text-secondary'}`} />
                                        : <Activity className={`w-6 h-6 ${isUp ? 'text-blue-400' : 'text-secondary'}`} />
                                    }
                                </div>
                                <div>
                                    <div className="flex items-center gap-2 mb-1">
                                        <h3 className="text-lg font-bold text-primary">{adapter.identity?.description}</h3>
                                        {adapter.status?.is_disabled ? (
                                            <span className="text-[10px] font-bold uppercase bg-orange-500/20 text-orange-500 px-2 py-0.5 rounded-md border border-orange-500/20">
                                                DISABLED
                                            </span>
                                        ) : (
                                            <span className={`text-[10px] uppercase font-bold px-1.5 py-0.5 rounded border 
                                                ${isUp ? 'bg-green-500/10 border-green-500/30 text-green-400' : 'bg-surface-3 border-white/5 text-secondary'}`}>
                                                {adapter.status?.oper_status}
                                            </span>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-4 text-xs text-secondary font-mono">
                                        <span>MAC: {adapter.identity?.mac}</span>
                                        {adapter.config?.ipv4 && <span className="text-primary">{adapter.config.ipv4}</span>}
                                    </div>
                                </div>
                            </div>

                            {/* Middle Column: Configuration Details */}
                            <div className="flex-1 w-full lg:w-auto grid grid-cols-2 gap-x-8 gap-y-2 text-xs">
                                <div className="flex justify-between border-b border-subtle pb-1">
                                    <span className="text-secondary">Link Speed</span>
                                    <span className="font-mono text-primary">{adapter.status?.link_speed || 'N/A'}</span>
                                </div>
                                <div className="flex justify-between border-b border-subtle pb-1">
                                    <span className="text-secondary">DHCP Enabled</span>
                                    <span className={`font-mono ${adapter.config?.dhcp_enabled ? 'text-primary' : 'text-orange-400'}`}>
                                        {adapter.config?.dhcp_enabled ? 'Yes' : 'No (Static)'}
                                    </span>
                                </div>
                                <div className="flex justify-between border-b border-subtle pb-1">
                                    <span className="text-secondary">Driver Date</span>
                                    <span className="font-mono text-primary">{adapter.driver?.date || 'Unknown'}</span>
                                </div>
                                <div className="flex justify-between border-b border-subtle pb-1">
                                    <span className="text-secondary">Gateway</span>
                                    <span className="font-mono text-primary">{adapter.config?.gateway || 'None'}</span>
                                </div>
                            </div>
                        </div>
                    );
                })}

                {/* Ledger */}
                <div className={`mt-4 flex-1 bg-surface-1 rounded-xl border transition-all flex flex-col overflow-hidden min-h-[300px] shrink-0
                        ${activeCard === 'ledger' ? 'border-blue-500/40' : 'border-subtle'}
                    `}>
                    <div className="p-4 border-b border-subtle flex justify-between items-center bg-surface-2/30">
                        <div className="flex items-center gap-2">
                            <FileText size={16} className="text-secondary" />
                            <h3 className="text-xs font-bold text-secondary uppercase tracking-wider">Network Stack Ledger</h3>
                        </div>
                        <div className="flex gap-2">
                            <span className="text-[10px] bg-surface-3 px-2 py-0.5 rounded text-secondary border border-white/5">Filtered: Tcpip, Netwtw, Dhcp</span>
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
                            const netEvents = events.filter((e: any) => {
                                const p = (e.ProviderName || '').toLowerCase();
                                return ['tcpip', 'netwtw', 'netbt', 'dhcp-client', 'dns-client'].some(k => p.includes(k));
                            }).sort((a: any, b: any) => new Date(b.TimeCreated).getTime() - new Date(a.TimeCreated).getTime());

                            if (netEvents.length === 0) {
                                return (
                                    <div className="flex flex-col items-center justify-center h-48 text-secondary/40">
                                        <Globe className="mb-2 opacity-50 w-8 h-8" />
                                        <span className="text-sm">No Network Events Detected</span>
                                        <span className="text-xs opacity-70">TCP/IP stack appears stable.</span>
                                    </div>
                                );
                            }

                            return (
                                <div className="divide-y divide-subtle">
                                    {netEvents.map((event: any) => (
                                        <div
                                            key={event.uniqueId || `evt-${event.Id}-${event.TimeCreated}`}
                                            onClick={() => {
                                                setActiveCard('ledger');
                                                setInspectableItem({
                                                    uniqueId: event.uniqueId,
                                                    type: 'event',
                                                    title: `Network Event: ${event.ProviderName}`,
                                                    description: event.Message,
                                                    data: event
                                                });
                                            }}
                                            className="grid grid-cols-12 gap-4 px-4 py-3 hover:bg-white/5 transition-colors cursor-pointer group text-xs ps-6"
                                        >
                                            <div className="col-span-2 font-mono text-secondary opacity-70">
                                                {new Date(event.TimeCreated).toLocaleTimeString()}
                                            </div>
                                            <div className="col-span-1">
                                                {event.Level === 2 || event.LevelDisplayName === 'Error'
                                                    ? <span className="font-bold text-red-400">ERR</span>
                                                    : event.Level === 3 || event.LevelDisplayName === 'Warning'
                                                        ? <span className="font-bold text-orange-400">WARN</span>
                                                        : <span className="text-secondary">INFO</span>
                                                }
                                            </div>
                                            <div className="col-span-2 text-secondary text-ellipsis overflow-hidden whitespace-nowrap" title={event.ProviderName}>
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
        </div>
    );
};
