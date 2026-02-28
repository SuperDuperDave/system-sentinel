import React, { useState } from 'react';
import { useSystemStore } from '@/lib/store';
import { useSelectionStore } from '@/lib/selectionStore';
import { useContextStore } from '../../context/ContextStore';
import { TopologyTree } from './components/TopologyTree';
import { Network, Activity, Info, PlusCircle, MinusCircle } from 'lucide-react';

export const PCIeView: React.FC = () => {
    const report = useSystemStore(state => state.diagnosticReport);
    const { setInspectableItem } = useSystemStore();
    const { select, isSelected, selectedIds, add, remove } = useSelectionStore();
    const { addItem, items, removeItemsBySourceId } = useContextStore(); // For Context Stack

    // Safety check: The report structure matches keys from Orchestrator (domains.pcie)
    // Legacy report structure had top-level 'topology'. V2.1 has 'domains.pcie'.
    // We need to handle both or migrate.
    // The current orchestrator outputs: { domains: { pcie: { raw, derived } } }

    const pcieData = report?.domains?.pcie || report?.topology; // Fallback or strict?
    // Wait, the orchestrator I wrote outputs: `domains: { pcie: ... }`.
    // The previous DeepDiagnosticsView expected legacy structure.
    // Dashboard.tsx fetchDiagnostics() fetches from `/api/system/diagnostics`.
    // This endpoint now returns the NEW V2.1 structure.
    // So `report.domains.pcie` is correct.

    // We need to fetch if missing.
    // Ideally use a specialized hook or button. For now, simple empty state.

    // Check if Fabric Context is already added
    const FABRIC_SOURCE_ID = 'pcie-fabric-full';
    const isFabricAdded = items.some(i => i.sourceId === FABRIC_SOURCE_ID);

    // Helper to toggle entire fabric context
    const toggleFabricContext = () => {
        if (!report?.domains?.pcie) return;

        if (isFabricAdded) {
            removeItemsBySourceId([FABRIC_SOURCE_ID]);
        } else {
            addItem({
                type: 'evidence',
                title: 'PCIe Fabric Topology',
                description: `Full PCIe enumeration (${report.domains.pcie.raw?.endpoints?.length || 0} endpoints).`,
                data: report.domains.pcie, // Use direct reference
                evidenceClass: 'raw',
                rank: 4,
                sourceId: FABRIC_SOURCE_ID
            });
        }
    };

    if (!report || !pcieData) {
        return (
            <div className="p-12 flex flex-col items-center justify-center text-secondary">
                <Network className="w-12 h-12 mb-4 opacity-20" />
                <h2 className="text-xl font-bold mb-2">No PCIe Context</h2>
                <p>Please run a Deep Scan from the Overview or Sidebar.</p>
            </div>
        );
    }

    // V2.1 Structure: pcieData = { raw: { roots, endpoints }, derived: { shared_groups } }
    const roots = pcieData.raw?.roots || pcieData.pci_roots || [];
    const endpoints = pcieData.raw?.endpoints || pcieData.endpoints || [];
    const sharedGroups = pcieData.derived?.shared_groups || [];

    const handleNodeSelect = (nodeOrNodes: any | any[], isMulti: boolean) => {
        const nodes = Array.isArray(nodeOrNodes) ? nodeOrNodes : [nodeOrNodes];

        nodes.forEach((node, index) => {
            const id = node.InstanceId || node.instance_id;
            // For batch selection, we want to accumulate (ctrl behavior) for all items
            // If it's a single click (not multi), the first item clears, others add?
            // Actually, useSelectionStore.select handles the toggle logic.
            // If we want to replace selection with this group, we might need a clear first?
            // Simpler: Just map select.
            select(id, { ctrl: isMulti || index > 0 });
        });

        // Inspect the principal item (first one or specific?)
        // If batch, maybe don't change inspector? Or inspect the root?
        if (nodes.length === 1) {
            const node = nodes[0];
            const id = node.InstanceId || node.instance_id;
            setInspectableItem({
                uniqueId: id,
                type: 'hardware',
                LevelDisplayName: node.status === 'Error' ? 'Critical' : 'Information',
                ProviderName: 'PCIeFabric',
                TimeCreated: new Date().toISOString(),
                Message: `PCIe Device: ${node.name || node.FriendlyName}`,
                ...node
            });
        }
    };

    // Add handleBranchToggle function
    const handleBranchToggle = (ids: string[], selected: boolean) => {
        if (selected) {
            add(ids);
        } else {
            remove(ids);
        }
    };

    return (
        <div className="h-full flex flex-col overflow-hidden bg-bg-app">
            {/* Header */}
            <div className="px-6 py-4 border-b border-subtle flex items-center justify-between bg-surface-1">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-blue-500/10 rounded-lg">
                        <Network className="w-5 h-5 text-blue-500" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-primary">PCIe Fabric & Topology</h1>
                        <p className="text-xs text-secondary">Physical Addressing (BDF) • Shared Groups • Upstream Chain</p>
                    </div>
                </div>
                <div className="text-xs text-tertiary font-mono">
                    {roots.length} Roots • {endpoints.length} Endpoints
                </div>
            </div>

            <div className="flex-1 grid grid-cols-12 gap-0 overflow-hidden">
                {/* Left: Physics Map (Tree) */}
                <div className="col-span-8 border-r border-subtle flex flex-col overflow-hidden">
                    <div className="px-4 py-2 bg-surface-2/50 border-b border-subtle flex justify-between items-center">
                        <div className="text-xs font-bold text-secondary uppercase tracking-wider">
                            Topology Map
                        </div>
                        <button
                            onClick={toggleFabricContext}
                            className={`flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wide transition-colors ${isFabricAdded
                                ? 'bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white'
                                : 'bg-blue-600 text-white hover:bg-blue-500'
                                }`}
                        >
                            {isFabricAdded ? <MinusCircle className="w-3 h-3" /> : <PlusCircle className="w-3 h-3" />}
                            {isFabricAdded ? 'Remove Context' : 'Add Fabric Context'}
                        </button>
                    </div>
                    <div className="flex-1 overflow-y-auto custom-scrollbar p-2">
                        <TopologyTree
                            roots={roots}
                            endpoints={endpoints}
                            onNodeSelect={handleNodeSelect}
                            onBranchToggle={handleBranchToggle}
                            selectedIds={Array.from(selectedIds)} // Pass as array
                        />
                    </div>
                </div>

                {/* Right: Shared Groups (New V2 Feature) */}
                <div className="col-span-4 flex flex-col overflow-hidden bg-surface-1/30">
                    <div className="flex-shrink-0 p-4 border-b border-subtle">
                        <div className="flex items-center gap-2 mb-2">
                            <Activity className="w-4 h-4 text-orange-500" />
                            <h3 className="font-bold text-sm text-primary">Shared Bandwidth Groups</h3>
                        </div>
                        <p className="text-xs text-secondary leading-snug">
                            Devices sharing the same Root Port compete for bandwidth and latency.
                            Deep Diagnostics V2 automatically detects these contention clusters.
                        </p>
                    </div>

                    <div className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-4">
                        {sharedGroups.length === 0 ? (
                            <div className="p-4 border border-dashed border-subtle rounded text-center text-xs text-secondary">
                                No multi-device shared groups detected.
                            </div>
                        ) : (
                            sharedGroups.map((group: any, i: number) => (
                                <div key={i} className="bg-surface-1 border border-subtle rounded-lg overflow-hidden">
                                    <div className="px-3 py-2 bg-surface-2 border-b border-subtle flex justify-between items-center">
                                        <span className="font-mono text-xs font-bold text-primary truncate max-w-[150px]" title={group.root_name}>
                                            {group.root_name || "Unknown Root"}
                                        </span>
                                        <span className="bg-orange-500/10 text-orange-500 text-[10px] px-1.5 py-0.5 rounded font-bold">
                                            {group.members.length} Devices
                                        </span>
                                    </div>
                                    <div className="p-2 space-y-1">
                                        {group.members.map((m: any, j: number) => {
                                            const name = typeof m === 'string' ? m : m.name;
                                            const id = typeof m === 'string' ? null : m.id;

                                            return (
                                                <div
                                                    key={j}
                                                    className={`
                                                        text-xs flex items-start gap-2 p-1 rounded transition-colors cursor-pointer
                                                        ${id && selectedIds.has(id) ? 'bg-blue-500/20 text-blue-400' : 'text-secondary hover:bg-surface-2 hover:text-primary'}
                                                    `}
                                                    onClick={(e) => {
                                                        const isMulti = e.ctrlKey || e.metaKey;
                                                        if (id) handleNodeSelect([{ InstanceId: id, name, status: 'Active' }], isMulti);
                                                    }}
                                                >
                                                    <span className="text-subtle mt-0.5">•</span>
                                                    <span className="truncate">{name}</span>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            ))
                        )}

                        {/* Info Card */}
                        <div className="mt-8 p-3 bg-blue-500/5 border border-blue-500/20 rounded-lg flex gap-3">
                            <Info className="w-4 h-4 text-blue-500 shrink-0 mt-0.5" />
                            <div className="text-xs text-blue-400">
                                <strong>Physics Note:</strong> Devices in shared groups cannot simultaneously saturate the bus without arbitrage latency.
                                Watch these groups for "stuttering" issues.
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};
