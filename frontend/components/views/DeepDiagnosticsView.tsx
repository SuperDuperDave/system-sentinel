import React, { useEffect, useState } from 'react';
import {
    Activity, Shield, Zap, AlertTriangle, ChevronRight, ChevronDown,
    Network, Clock, Cpu, Search, AlertOctagon, Target, Sliders, CheckCircle
} from 'lucide-react';
import { useSystemStore } from '@/lib/store';
import { useSelectionStore } from '@/lib/selectionStore';
import { getApiBase } from '@/lib/api';

// Updated Types matching "Token-Smart" Backend
interface DiagnosticReport {
    meta: { timestamp: string; version: string };
    fingerprint: {
        cpu_model: string;
        bios_ver: string;
        board_model: string;
        ram_total_gb: number;
        ram_speed_mtps: number;
    };
    topology: {
        pci_roots: any[];
        endpoints: any[];
    };
    power: {
        active_scheme_guid: string;
        pcie_aspm: { ac_index_hex: string; dc_index_hex: string, decoded_ac: string, raw_excerpt?: string };
        fast_startup: boolean;
        uptime_seconds: number;
        last_boot_utc: string;
    };
    memory: {
        total_gb: number;
        oc_suspicion: { value: boolean; confidence: number; basis?: string[] };
        ddr_generation?: string;
    };
    // New Timeline Format
    timeline_summary: {
        window_minutes: number;
        counts_by_provider: Record<string, number>;
        markers: { had_sleep: boolean; had_wake: boolean; had_gpu_reset: boolean; had_storage_timeout: boolean };
    };
    timeline_events: any[]; // Was critical_last_50, now Top N

    // New Sections
    incident: {
        event_id: number;
        time_created: string;
        provider: string;
        decoded_fields: { message: string, severity?: string };
    } | null;

    aggressor_candidates: Array<{
        device: string;
        reason: string;
        confidence: number;
        details?: string;
    }>;

    user_actions: {
        constraints: Array<{
            type: string;
            instance_id: string;
            friendly_name: string;
            reason: string;
        }>;
    };
}

// --- SUBSIDIARY COMPONENTS ---

const TreeNode = ({ node, childrenMap, level = 0, isRoot = false }: { node: any, childrenMap: Map<string, any[]>, level?: number, isRoot?: boolean }) => {
    const [expanded, setExpanded] = useState(true); // Default expand
    const nodeId = node.InstanceId || node.instance_id;
    const children = childrenMap.get(nodeId) || [];
    const hasChildren = children.length > 0;

    // ID Formatter
    const displayId = nodeId ? (nodeId.includes('\\') ? nodeId.split('\\')[1] : nodeId) : '';

    return (
        <div className={`transition-all ${level > 0 ? 'ml-3 pl-3 border-l border-white/5' : ''}`}>
            {/* Node Card - UNIFIED STYLE */}
            <div
                className={`
                    group relative flex items-start gap-2 p-2.5 rounded-lg mb-2
                    transition-all cursor-pointer border bg-surface-1 border-subtle
                    ${node.is_user_disabled ? 'opacity-75 border-orange-500/30' : 'hover:border-blue-500/50'}
                `}
                onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
            >
                {/* Expand Toggle */}
                <div className={`mt-0.5 flex-shrink-0 w-4 h-4 flex items-center justify-center rounded hover:bg-white/10 ${hasChildren ? 'visible' : 'invisible'}`}>
                    {expanded ? <ChevronDown size={14} className="text-secondary" /> : <ChevronRight size={14} className="text-secondary" />}
                </div>

                {/* Content Container */}
                <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-0.5">
                        <span className="text-sm font-bold text-primary truncate w-5/6">
                            {node.name || node.Name || node.FriendlyName}
                        </span>

                        {/* Status Pills */}
                        {node.is_user_disabled && <span className="text-[10px] text-orange-500 font-bold uppercase border border-orange-500/20 px-1 rounded">Disabled</span>}
                        {!node.is_user_disabled && node.status && (
                            <span className={`text-[10px] px-1.5 py-0.5 rounded ${node.status === 'OK' ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-500'}`}>
                                {node.status === 'Error' ? 'Err' : node.status}
                            </span>
                        )}
                    </div>

                    {/* Meta / Classes / ID */}
                    <div className="text-xs text-secondary truncate flex items-center gap-2 opacity-70 group-hover:opacity-100 transition-opacity">
                        {isRoot ? (
                            <span className="uppercase tracking-wider text-[10px] font-bold">Root Port</span>
                        ) : (
                            node.class && <span>{node.class}</span>
                        )}
                        {displayId && <span className="opacity-50 text-[10px] font-mono">• {displayId}</span>}
                    </div>
                </div>
            </div>

            {/* Recursion */}
            {expanded && hasChildren && (
                <div className="mt-1">
                    {children.map((child, i) => (
                        <TreeNode key={i} node={child} childrenMap={childrenMap} level={level + 1} />
                    ))}
                </div>
            )}
        </div>
    );
};

const TopologyTree = ({ roots, endpoints }: { roots: any[], endpoints: any[] }) => {
    // Build Parent->Children Map
    const childrenMap = React.useMemo(() => {
        const map = new Map<string, any[]>();

        // Populate Children
        endpoints.forEach(ep => {
            if (ep.parent_instance_id) {
                if (!map.has(ep.parent_instance_id)) {
                    map.set(ep.parent_instance_id, []);
                }
                map.get(ep.parent_instance_id)?.push(ep);
            }
        });

        return map;
    }, [roots, endpoints]);

    return (
        <div className="p-2 select-none">
            {roots.length === 0 && <div className="text-xs text-secondary italic p-4">No Root Ports found.</div>}

            {roots.map((root, i) => (
                <TreeNode key={i} node={root} childrenMap={childrenMap} isRoot={true} />
            ))}
        </div>
    );
};

export const DeepDiagnosticsView: React.FC = () => {
    const [data, setData] = useState<DiagnosticReport | null>(null);
    const [loading, setLoading] = useState(true);
    const { setDiagnosticReport, setInspectableItem } = useSystemStore();
    const { select, isSelected } = useSelectionStore();

    const fetchDiagnostics = async () => {
        setLoading(true);
        try {
            const res = await fetch(`${getApiBase()}/api/system/diagnostics`);
            const json = await res.json();
            setData(json);

            // Granular Chunks
            const chunks = [
                { uniqueId: 'diag-identity', type: 'diagnostic', title: 'Identity & Fingerprint', data: json.fingerprint, ProviderName: 'DeepDiagnostics' },
                { uniqueId: 'diag-topology', type: 'diagnostic', title: 'PCIe Topology (Derived Tree)', data: json.topology, ProviderName: 'DeepDiagnostics' },
                { uniqueId: 'diag-env', type: 'diagnostic', title: 'Power, Memory & Environment', data: { power: json.power, memory: json.memory, user_actions: json.user_actions }, ProviderName: 'DeepDiagnostics' },
                { uniqueId: 'diag-timeline', type: 'diagnostic', title: 'Timeline Analysis', data: { summary: json.timeline_summary, events: json.timeline_events }, ProviderName: 'DeepDiagnostics' }
            ];

            if (json.incident) {
                chunks.unshift({ uniqueId: 'diag-incident', type: 'diagnostic', title: 'Target Incident (WHEA)', data: json.incident, ProviderName: 'DeepDiagnostics' });
            }
            if (json.aggressor_candidates?.length > 0) {
                chunks.push({ uniqueId: 'diag-aggressors', type: 'diagnostic', title: 'Aggressor Candidates', data: json.aggressor_candidates, ProviderName: 'DeepDiagnostics' });
            }

            setDiagnosticReport(chunks);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchDiagnostics();
    }, []);

    const handleSectionClick = (e: React.MouseEvent, chunkId: string, title: string, content: any) => {
        e.stopPropagation();
        if (e.ctrlKey) {
            select(chunkId, { ctrl: true });
        } else {
            select(chunkId, { ctrl: false });
        }

        setInspectableItem({
            uniqueId: chunkId,
            type: 'diagnostic',
            Message: `Deep Diagnostic Source: ${title}`,
            ProviderName: 'DeepDiagnostics',
            TimeCreated: new Date().toISOString(),
            LevelDisplayName: 'Information',
            title: title,
            data: content
        });
    };

    if (loading) {
        return (
            <div className="flex flex-col items-center justify-center h-full text-secondary gap-4">
                <Activity className="w-8 h-8 animate-spin text-blue-500" />
                <div className="text-sm font-medium">Running Token-Smart Hardware Scan...</div>
            </div>
        );
    }

    if (!data) return <div>Failed to load diagnostics.</div>;

    // Partial data check - if stable scan failed, we might only have volatile data
    const hasStable = !!data.fingerprint && !!data.topology;

    const getBorderClass = (id: string) => isSelected(id) ? 'border-blue-500 ring-1 ring-blue-500 bg-blue-500/5' : 'border-subtle hover:border-blue-500/50';

    return (
        <div className="flex flex-col h-full bg-app overflow-hidden">
            {/* Header */}
            <header
                onClick={(e) => hasStable ? handleSectionClick(e, 'diag-identity', 'Identity', data.fingerprint) : null}
                className={`flex items-center gap-6 px-6 py-4 border-b bg-surface-1 cursor-pointer transition-colors ${hasStable ? getBorderClass('diag-identity') : 'opacity-50 cursor-not-allowed'}`}
            >
                <div className="flex flex-col">
                    <h2 className="text-xl font-bold text-primary tracking-tight">System Diagnostics</h2>
                    <div className="flex items-center gap-3 text-xs text-secondary mt-1">
                        {hasStable ? (
                            <>
                                <span className="flex items-center gap-1"><Cpu className="w-3 h-3" /> {data.fingerprint.cpu_model}</span>
                                <span className="w-px h-3 bg-subtle" />
                                <span>BIOS {data.fingerprint.bios_ver}</span>
                            </>
                        ) : (
                            <span className="text-orange-500">Stable Hardware Context Missing (Scan Error?)</span>
                        )}
                    </div>
                </div>
                <div className="ml-auto flex items-center gap-4">
                    {/* Incident Badge */}
                    {data.incident && (
                        <div
                            onClick={(e) => handleSectionClick(e, 'diag-incident', 'Target Incident', data.incident)}
                            className={`flex items-center gap-2 px-3 py-1.5 rounded bg-red-500/10 border border-red-500/50 text-red-500 cursor-pointer ${getBorderClass('diag-incident')}`}
                        >
                            <AlertOctagon className="w-4 h-4" />
                            <span className="text-xs font-bold">Fatal WHEA Found</span>
                        </div>
                    )}

                    <div className="flex flex-col items-end">
                        <span className="text-xs font-bold text-secondary uppercase tracking-wider">Memory</span>
                        {hasStable ? (
                            <span className={`text-sm font-mono ${data.memory.oc_suspicion.value ? 'text-orange-500' : 'text-primary'}`}>
                                {data.fingerprint.ram_total_gb}GB @ {data.fingerprint.ram_speed_mtps} MT/s
                                {data.memory.oc_suspicion.value && " (OC?)"}
                            </span>
                        ) : (
                            <span className="text-sm font-mono text-tertiary">--</span>
                        )}
                    </div>
                    <button
                        onClick={(e) => { e.stopPropagation(); fetchDiagnostics(); }}
                        className="p-2 hover:bg-surface-2 rounded-lg transition-colors"
                        title="Re-scan"
                    >
                        <Activity className="w-4 h-4 text-tertiary" />
                    </button>
                </div>
            </header>

            {/* 3-Column Layout */}
            <div className="flex-1 grid grid-cols-12 gap-0 overflow-hidden">

                {/* COL 1: TOPOLOGY */}
                <div
                    onClick={(e) => hasStable ? handleSectionClick(e, 'diag-topology', 'PCIe Topology', data.topology) : null}
                    className={`col-span-4 border-r flex flex-col overflow-hidden transition-colors cursor-pointer ${hasStable ? getBorderClass('diag-topology') : 'opacity-50'}`}
                >
                    <div className="px-4 py-2 bg-surface-2/50 border-b border-subtle text-xs font-bold text-secondary uppercase tracking-wider flex items-center gap-2">
                        <Network className="w-3 h-3" /> PCIe Fabric Map
                    </div>
                    <div className="flex-1 overflow-y-auto p-2 space-y-2 custom-scrollbar">
                        {!hasStable ? (
                            <div className="p-4 text-sm text-secondary">Topology scan unavailable.</div>
                        ) : (
                            <TopologyTree roots={data.topology.pci_roots || []} endpoints={data.topology.endpoints || []} />
                        )}
                    </div>
                </div>

                {/* COL 2: ENV & AGGRESSORS */}
                <div className="col-span-4 border-r flex flex-col overflow-hidden bg-surface-1/30">
                    <div
                        onClick={(e) => handleSectionClick(e, 'diag-env', 'Power & Environment', { power: data.power, memory: data.memory, user_actions: data.user_actions })}
                        className={`flex-shrink-0 cursor-pointer transition-colors ${getBorderClass('diag-env')}`}
                    >
                        <div className="px-4 py-2 bg-surface-2/50 border-b border-subtle text-xs font-bold text-secondary uppercase tracking-wider flex items-center gap-2">
                            <Shield className="w-3 h-3" /> Environment
                        </div>
                        <div className="p-4 space-y-4">
                            {/* User Actions / Constraints */}
                            {data.user_actions?.constraints?.length > 0 && (
                                <div className="p-3 border border-blue-500/30 bg-blue-500/5 rounded-lg">
                                    <div className="flex items-center gap-2 mb-2 text-blue-400">
                                        <Sliders className="w-3.5 h-3.5" />
                                        <span className="font-bold text-xs uppercase">User Constraints Detected</span>
                                    </div>
                                    <div className="space-y-1">
                                        {data.user_actions.constraints.map((c, i) => (
                                            <div key={i} className="text-xs text-primary flex gap-2">
                                                <span className="text-secondary">•</span>
                                                <span>{c.friendly_name} <span className="opacity-50">(Manual Disable)</span></span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            <div className="grid grid-cols-2 gap-2">
                                <div className="p-3 bg-surface-1 border border-subtle rounded text-center">
                                    <div className="text-xs text-secondary mb-1">ASPM (AC)</div>
                                    <div className="font-mono text-sm font-bold text-primary">
                                        {data.power?.pcie_aspm?.decoded_ac || "N/A"}
                                    </div>
                                    <div className="text-[10px] text-tertiary mt-1 opacity-75 font-mono">{data.power?.pcie_aspm?.ac_index_hex || "0x?"}</div>
                                </div>
                                <div className="p-3 bg-surface-1 border border-subtle rounded text-center">
                                    <div className="text-xs text-secondary mb-1">Fast Startup</div>
                                    <div className={`font-mono text-sm font-bold ${data.power?.fast_startup ? 'text-orange-500' : 'text-green-500'}`}>
                                        {data.power?.fast_startup !== undefined ? (data.power.fast_startup ? 'ENABLED' : 'DISABLED') : 'N/A'}
                                    </div>
                                    <div className="text-[10px] text-tertiary mt-1">Kernel Hibernation</div>
                                </div>
                            </div>

                            {hasStable && data.memory ? (
                                <div className={`p-3 border rounded-lg ${data.memory.oc_suspicion.value ? 'bg-orange-500/5 border-orange-500/30' : 'bg-green-500/5 border-green-500/30'}`}>
                                    <div className="flex items-center gap-2 mb-1">
                                        <Zap className={`w-3.5 h-3.5 ${data.memory.oc_suspicion.value ? 'text-orange-500' : 'text-green-500'}`} />
                                        <span className="font-bold text-xs">
                                            {data.memory.oc_suspicion.value ? 'Overclock Detected' : 'Safety Profile'}
                                        </span>
                                    </div>
                                    {data.memory.oc_suspicion.basis && data.memory.oc_suspicion.basis.length > 0 ? (
                                        <div className="text-[10px] text-secondary leading-tight mt-1 space-y-1">
                                            {data.memory.oc_suspicion.basis.map((b: string, i: number) => (
                                                <div key={i} className="flex gap-1"><span>•</span> {b}</div>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="text-[10px] text-secondary leading-tight mt-1 opacity-50">
                                            Running within JEDEC specifications.
                                        </div>
                                    )}
                                </div>
                            ) : null}
                        </div>
                    </div>

                    {/* Aggressors */}
                    {data.aggressor_candidates && data.aggressor_candidates.length > 0 && (
                        <div
                            onClick={(e) => handleSectionClick(e, 'diag-aggressors', 'Aggressor Candidates', data.aggressor_candidates)}
                            className={`flex-1 flex flex-col cursor-pointer transition-colors border-t border-subtle ${getBorderClass('diag-aggressors')}`}
                        >
                            <div className="px-4 py-2 bg-surface-2/50 border-b border-subtle text-xs font-bold text-orange-500 uppercase tracking-wider flex items-center gap-2">
                                <Target className="w-3 h-3" /> Aggressor Candidates
                            </div>
                            <div className="p-2 space-y-2 overflow-y-auto custom-scrollbar">
                                {data.aggressor_candidates.map((cand, i) => (
                                    <div key={i} className="p-2 bg-surface-1 border border-orange-500/20 rounded shadow-sm">
                                        <div className="flex justify-between items-center mb-1">
                                            <span className="font-bold text-xs text-primary">{cand.device}</span>
                                            <span className="text-[10px] font-mono bg-orange-500/10 text-orange-500 px-1.5 rounded">{(cand.confidence * 100).toFixed(0)}% Conf</span>
                                        </div>
                                        <div className="text-[10px] text-secondary">{cand.reason}</div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* COL 3: TIMELINE (Token-Smart) */}
                <div
                    onClick={(e) => handleSectionClick(e, 'diag-timeline', 'Timeline Analysis', { summary: data.timeline_summary, events: data.timeline_events })}
                    className={`col-span-4 flex flex-col overflow-hidden transition-colors cursor-pointer ${getBorderClass('diag-timeline')}`}
                >
                    <div className="px-4 py-2 bg-surface-2/50 border-b border-subtle text-xs font-bold text-secondary uppercase tracking-wider flex items-center gap-2">
                        <Clock className="w-3 h-3" /> Timeline Analysis
                    </div>

                    {/* Summary Cards */}
                    <div className="p-3 grid grid-cols-2 gap-2 border-b border-subtle bg-surface-1/50">
                        <div className="p-2 bg-surface-1 rounded border border-subtle">
                            <div className="text-[10px] text-secondary uppercase tracking-wider mb-1">Window</div>
                            <div className="text-xs font-bold text-primary">{data.timeline_summary.window_minutes} Minutes</div>
                        </div>
                        <div className="p-2 bg-surface-1 rounded border border-subtle">
                            <div className="text-[10px] text-secondary uppercase tracking-wider mb-1">Key Markers</div>
                            <div className="flex gap-1">
                                {data.timeline_summary.markers.had_sleep && <span title="Sleep" className="w-2 h-2 rounded-full bg-blue-500"></span>}
                                {data.timeline_summary.markers.had_wake && <span title="Wake" className="w-2 h-2 rounded-full bg-green-500"></span>}
                                {data.timeline_summary.markers.had_gpu_reset && <span title="GPU Reset" className="w-2 h-2 rounded-full bg-red-500"></span>}
                                {data.timeline_summary.markers.had_storage_timeout && <span title="Storage Timeout" className="w-2 h-2 rounded-full bg-orange-500"></span>}
                                {!Object.values(data.timeline_summary.markers).some(Boolean) && <span className="text-[10px] text-tertiary">None</span>}
                            </div>
                        </div>
                    </div>

                    <div className="flex-1 overflow-y-auto p-2 space-y-2 custom-scrollbar">
                        {(!data.timeline_events || !Array.isArray(data.timeline_events) || data.timeline_events.length === 0) ? (
                            <div className="p-8 text-center text-sm text-secondary opacity-50">
                                {data.timeline_events && !Array.isArray(data.timeline_events) ? "Single event returned (check Inspector)" : "No significant events in window."}
                            </div>
                        ) : (
                            data.timeline_events.map((evt: any, i: number) => (
                                <div key={i} className="flex gap-3 p-3 rounded-lg hover:bg-surface-2 group">
                                    <div className="mt-0.5">
                                        {evt.LevelDisplayName === 'Critical' || evt.LevelDisplayName === 'Error' ? (
                                            <AlertTriangle className="w-4 h-4 text-red-500" />
                                        ) : (
                                            <Activity className="w-4 h-4 text-secondary" />
                                        )}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center justify-between gap-2 mb-0.5">
                                            <span className="text-xs font-bold text-primary truncate">{evt.ProviderName}</span>
                                            <span className="text-[10px] text-tertiary whitespace-nowrap">
                                                {new Date(evt.TimeCreated).toLocaleTimeString()}
                                            </span>
                                        </div>
                                        <div className="text-xs text-secondary leading-snug line-clamp-2">
                                            {evt.Message}
                                        </div>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </div>

            </div>
        </div>
    );
};
