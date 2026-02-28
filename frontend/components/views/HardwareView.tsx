import React from 'react';
import { Cpu, Server, CircuitBoard, HardDrive, AlertTriangle, Shield, Zap, Activity } from 'lucide-react';
import { useSystemStore } from '@/lib/store';
import { useContextStore } from '../context/ContextStore';
import { useSelectionStore } from '@/lib/selectionStore';

export const HardwareView: React.FC = () => {
    const report = useSystemStore(state => state.diagnosticReport);
    const { setInspectableItem } = useSystemStore();
    const { clearSelection } = useSelectionStore();
    const { addItem, items, removeItemsBySourceId } = useContextStore();

    // Access the new Platform Model data
    const hardware = report?.domains?.hardware;

    if (!report || !hardware) {
        return (
            <div className="p-12 flex flex-col items-center justify-center text-secondary">
                <CircuitBoard className="w-12 h-12 mb-4 opacity-20" />
                <h2 className="text-xl font-bold mb-2">No Platform Model</h2>
                <p>Please run a Deep Scan to generate the hardware forensics model.</p>
            </div>
        );
    }

    const { fingerprint, config, risks } = hardware;

    // Helper for context management
    const handleAddContext = (title: string, data: any) => {
        const sourceId = `hw-${title.toLowerCase()}`;
        if (items.some(i => i.sourceId === sourceId)) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title: `Hardware: ${title}`,
                description: `Platform Identity for ${title}`,
                data,
                evidenceClass: 'invariant',
                rank: 5,
                sourceId
            });
        }
    };

    return (
        <div className="flex flex-col h-full w-full overflow-hidden bg-bg-app">

            {/* Header */}
            <header className="px-6 py-4 border-b border-subtle flex items-center justify-between bg-surface-1 shrink-0">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-blue-500/10 rounded-lg">
                        <CircuitBoard className="w-5 h-5 text-blue-500" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-primary">Platform Model</h1>
                        <p className="text-xs text-secondary">Forensic Identity • Configuration • Risk Posture</p>
                    </div>
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={() => handleAddContext("Full Platform", hardware)}
                        className="px-3 py-1.5 bg-surface-2 hover:bg-surface-3 border border-subtle rounded text-xs font-bold text-secondary transition-colors"
                    >
                        Add Full Context
                    </button>
                </div>
            </header>

            <div className="p-6 overflow-y-auto custom-scrollbar space-y-6">

                {/* High Fidelity Tiles Grid */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">

                    {/* CPU Tile */}
                    <div
                        className="group bg-surface-1 border border-subtle rounded-xl p-5 hover:border-blue-500/30 transition-all cursor-pointer relative overflow-hidden"
                        onClick={() => {
                            clearSelection();
                            setInspectableItem({
                                type: 'hardware',
                                title: fingerprint.cpu?.name,
                                panelTitle: "CPU Platform",
                                description: "Processor Identity & Virtualization Posture",
                                data: { identity: fingerprint.cpu, config: { virtualization: config?.virtualization_firmware, hyperv: config?.hyperv_running } },
                                LevelDisplayName: 'Identity',
                                ProviderName: 'PlatformModel',
                                TimeCreated: hardware.timestamp
                            });
                        }}
                    >
                        <div className="flex justify-between items-start mb-4">
                            <div className="p-2 bg-surface-2 rounded-lg group-hover:bg-blue-500/10 group-hover:text-blue-400 transition-colors">
                                <Cpu className="w-6 h-6" />
                            </div>
                            {config?.virtualization_firmware ? (
                                <span className="text-[10px] bg-green-500/10 text-green-500 px-2 py-0.5 rounded font-mono">VIRT CHECK</span>
                            ) : (
                                <span className="text-[10px] bg-red-500/10 text-red-500 px-2 py-0.5 rounded font-mono">VIRT DISABLED</span>
                            )}
                        </div>
                        <h3 className="text-primary font-bold truncate mb-1">{fingerprint.cpu?.name}</h3>
                        <p className="text-xs text-secondary mb-4">{fingerprint.cpu?.description}</p>

                        <div className="grid grid-cols-2 gap-2 text-xs border-t border-white/5 pt-3">
                            <div>
                                <span className="text-tertiary block mb-0.5">Cores / Threads</span>
                                <span className="font-mono text-primary">{fingerprint.cpu?.cores}C / {fingerprint.cpu?.logical}T</span>
                            </div>
                            <div>
                                <span className="text-tertiary block mb-0.5">Hyper-V</span>
                                <span className={`font-mono ${config?.hyperv_running ? 'text-blue-400' : 'text-tertiary'}`}>
                                    {config?.hyperv_running ? 'ACTIVE' : 'INACTIVE'}
                                </span>
                            </div>
                        </div>
                    </div>

                    {/* GPU Tile */}
                    <div
                        className="group bg-surface-1 border border-subtle rounded-xl p-5 hover:border-purple-500/30 transition-all cursor-pointer relative overflow-hidden"
                        onClick={() => {
                            clearSelection();
                            setInspectableItem({
                                type: 'hardware',
                                title: fingerprint.gpu?.name,
                                panelTitle: "Graphics Adapter",
                                description: "GPU Identity & Driver Provenance",
                                data: fingerprint.gpu,
                                LevelDisplayName: 'Identity',
                                ProviderName: 'PlatformModel',
                                TimeCreated: hardware.timestamp
                            });
                        }}
                    >
                        <div className="flex justify-between items-start mb-4">
                            <div className="p-2 bg-surface-2 rounded-lg group-hover:bg-purple-500/10 group-hover:text-purple-400 transition-colors">
                                <Activity className="w-6 h-6" />
                            </div>
                            <span className="text-[10px] bg-surface-2 text-secondary px-2 py-0.5 rounded font-mono">
                                {fingerprint.gpu?.vram_mb > 0 ? `${Math.round(fingerprint.gpu?.vram_mb / 1024)}GB VRAM` : 'INTEGRATED'}
                            </span>
                        </div>
                        <h3 className="text-primary font-bold truncate mb-1">{fingerprint.gpu?.name}</h3>
                        <p className="text-xs text-secondary mb-4">Driver: {fingerprint.gpu?.driver_version}</p>

                        <div className="grid grid-cols-2 gap-2 text-xs border-t border-white/5 pt-3">
                            <div>
                                <span className="text-tertiary block mb-0.5">Driver Date</span>
                                <span className="font-mono text-primary">{fingerprint.gpu?.date || 'Unknown'}</span>
                            </div>
                            <div>
                                <span className="text-tertiary block mb-0.5">WDDM</span>
                                <span className="font-mono text-primary">--</span>
                            </div>
                        </div>
                    </div>

                    {/* Motherboard Tile */}
                    <div
                        className="group bg-surface-1 border border-subtle rounded-xl p-5 hover:border-orange-500/30 transition-all cursor-pointer relative overflow-hidden"
                        onClick={() => {
                            clearSelection();
                            setInspectableItem({
                                type: 'hardware',
                                title: fingerprint.board?.product,
                                panelTitle: "Mainboard & Firmware",
                                description: "Motherboard Identity, BIOS & Security Features.",
                                data: { ...fingerprint.board, secure_boot: config?.secure_boot, fast_startup: config?.fast_startup },
                                LevelDisplayName: 'Identity',
                                ProviderName: 'PlatformModel',
                                TimeCreated: hardware.timestamp
                            });
                        }}
                    >
                        <div className="flex justify-between items-start mb-4">
                            <div className="p-2 bg-surface-2 rounded-lg group-hover:bg-orange-500/10 group-hover:text-orange-400 transition-colors">
                                <CircuitBoard className="w-6 h-6" />
                            </div>
                            {config?.secure_boot ? (
                                <div className="flex items-center gap-1.5 text-[10px] bg-green-500/10 text-green-500 px-2 py-0.5 rounded font-mono">
                                    <Shield size={10} /> SECURE BOOT
                                </div>
                            ) : (
                                <div className="flex items-center gap-1.5 text-[10px] bg-red-500/10 text-red-500 px-2 py-0.5 rounded font-mono">
                                    <AlertTriangle size={10} /> INSECURE
                                </div>
                            )}
                        </div>
                        <h3 className="text-primary font-bold truncate mb-1">{fingerprint.board?.manufacturer} {fingerprint.board?.product}</h3>
                        <p className="text-xs text-secondary mb-4">BIOS: {fingerprint.board?.bios_version} ({fingerprint.board?.bios_date})</p>

                        <div className="grid grid-cols-2 gap-2 text-xs border-t border-white/5 pt-3">
                            <div>
                                <span className="text-tertiary block mb-0.5">Fast Startup</span>
                                <span className={`font-mono ${config?.fast_startup ? 'text-red-400' : 'text-green-400'}`}>
                                    {config?.fast_startup ? 'ENABLED (RISK)' : 'DISABLED'}
                                </span>
                            </div>
                            <div>
                                <span className="text-tertiary block mb-0.5">Version</span>
                                <span className="font-mono text-primary">{fingerprint.board?.version}</span>
                            </div>
                        </div>
                    </div>

                    {/* Storage Tile */}
                    <div
                        className="group bg-surface-1 border border-subtle rounded-xl p-5 hover:border-emerald-500/30 transition-all cursor-pointer relative overflow-hidden"
                        onClick={() => {
                            clearSelection();
                            setInspectableItem({
                                type: 'hardware',
                                title: fingerprint.storage?.boot_model,
                                panelTitle: "Boot Storage",
                                description: "System Drive Identity & Capacity.",
                                data: fingerprint.storage,
                                LevelDisplayName: 'Identity',
                                ProviderName: 'PlatformModel',
                                TimeCreated: hardware.timestamp
                            });
                        }}
                    >
                        <div className="flex justify-between items-start mb-4">
                            <div className="p-2 bg-surface-2 rounded-lg group-hover:bg-emerald-500/10 group-hover:text-emerald-400 transition-colors">
                                <HardDrive className="w-6 h-6" />
                            </div>
                            <span className={`text-[10px] px-2 py-0.5 rounded font-mono ${fingerprint.storage?.free_percent < 10 ? 'bg-red-500/10 text-red-500' : 'bg-surface-2 text-secondary'}`}>
                                {fingerprint.storage?.free_percent}% FREE
                            </span>
                        </div>
                        <h3 className="text-primary font-bold truncate mb-1">{fingerprint.storage?.boot_model}</h3>
                        <p className="text-xs text-secondary mb-4">{fingerprint.storage?.size_gb} GB • {fingerprint.storage?.media_type} • {fingerprint.storage?.interface}</p>

                        <div className="w-full bg-black/20 h-1.5 rounded-full overflow-hidden">
                            <div
                                className={`h-full rounded-full ${fingerprint.storage?.free_percent < 10 ? 'bg-red-500' : 'bg-emerald-500'}`}
                                style={{ width: `${100 - fingerprint.storage?.free_percent}%` }}
                            />
                        </div>
                        <div className="mt-2 text-[10px] text-tertiary flex justify-between">
                            <span>Used</span>
                            <span>{100 - fingerprint.storage?.free_percent}%</span>
                        </div>
                    </div>

                    {/* Network Tile */}
                    {hardware.details?.network && (
                        <div
                            className="group bg-surface-1 border border-subtle rounded-xl p-5 hover:border-blue-500/30 transition-all cursor-pointer relative overflow-hidden"
                            onClick={() => {
                                clearSelection();
                                // Handle missing network gracefully
                                const primary = hardware.details.network?.adapters?.find((a: any) => a.status?.oper_status === 'Up') || hardware.details.network?.adapters?.[0] || {};
                                setInspectableItem({
                                    type: 'hardware',
                                    title: primary.identity?.description || 'Network Adapter',
                                    panelTitle: "Network Connectivity",
                                    description: "Network Interface Identity & Config",
                                    data: primary,
                                    LevelDisplayName: 'Identity',
                                    ProviderName: 'PlatformModel',
                                    TimeCreated: hardware.timestamp
                                });
                            }}
                        >
                            <div className="flex justify-between items-start mb-4">
                                <div className="p-2 bg-surface-2 rounded-lg group-hover:bg-blue-500/10 group-hover:text-blue-400 transition-colors">
                                    <Activity className="w-6 h-6" />
                                </div>
                                {(hardware.details.network?.risks || []).length > 0 ? (
                                    <div className="flex items-center gap-1.5 text-[10px] bg-red-500/10 text-red-500 px-2 py-0.5 rounded font-mono">
                                        <AlertTriangle size={10} /> RISK DETECTED
                                    </div>
                                ) : (
                                    <div className="flex items-center gap-1.5 text-[10px] bg-green-500/10 text-green-500 px-2 py-0.5 rounded font-mono">
                                        <Shield size={10} /> SECURE
                                    </div>
                                )}
                            </div>
                            <h3 className="text-primary font-bold truncate mb-1">Network Connectivity</h3>
                            <p className="text-xs text-secondary mb-4">{(hardware.details.network?.adapters || []).length} Adapters Active</p>

                            <div className="grid grid-cols-2 gap-2 text-xs border-t border-white/5 pt-3">
                                <div>
                                    <span className="text-tertiary block mb-0.5">Primary Link</span>
                                    <span className="font-mono text-primary">
                                        {hardware.details.network?.adapters?.find((a: any) => a.status?.oper_status === 'Up')?.status?.link_speed || '--'}
                                    </span>
                                </div>
                                <div>
                                    <span className="text-tertiary block mb-0.5">Driver Date</span>
                                    <span className="font-mono text-primary">
                                        {hardware.details.network?.adapters?.[0]?.driver?.date || '--'}
                                    </span>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* Insights Queue (Moved Risks) */}
                {risks && risks.length > 0 && (
                    <div className="mt-8">
                        <div className="flex items-center gap-2 mb-4 border-b border-subtle pb-2">
                            <AlertTriangle className="w-4 h-4 text-secondary" />
                            <h3 className="text-sm font-bold text-secondary uppercase tracking-wider">Insights Queue</h3>
                        </div>
                        <div className="grid grid-cols-1 gap-3">
                            {risks.map((risk: any, idx: number) => (
                                <div key={idx} className="bg-red-500/5 border border-red-500/20 p-4 rounded-lg flex items-center justify-between group hover:bg-red-500/10 transition-colors cursor-pointer">
                                    <div className="flex items-start gap-4">
                                        <div className="p-2 bg-red-500/10 rounded-lg text-red-500">
                                            <AlertTriangle className="w-5 h-5" />
                                        </div>
                                        <div>
                                            <h4 className="font-bold text-red-400 text-sm mb-0.5">Platform Risk Detected</h4>
                                            <p className="text-sm text-secondary group-hover:text-primary transition-colors">{risk.message}</p>
                                        </div>
                                    </div>
                                    <div className="text-xs font-mono text-red-500/50 group-hover:text-red-500 transition-colors uppercase border border-red-500/20 px-2 py-1 rounded">
                                        Critical
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
