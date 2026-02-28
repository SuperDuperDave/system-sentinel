import React, { useState, useMemo } from 'react';
import { ChevronRight, ChevronDown, Cpu, HardDrive, Network, Monitor, Zap, Box, Layers, ListTree, ToggleLeft, ToggleRight } from 'lucide-react';

// Helper to determine icon based on device class or name
const getDeviceIcon = (node: any) => {
    const name = (node.name || node.FriendlyName || '').toLowerCase();
    const cls = (node.class || '').toLowerCase();

    if (cls.includes('display') || name.includes('nvidia') || name.includes('graphics')) return <Monitor className="w-5 h-5 text-purple-400" />;
    if (cls.includes('storage') || cls.includes('nvm') || name.includes('ssd')) return <HardDrive className="w-5 h-5 text-green-400" />;
    if (cls.includes('network') || cls.includes('ethernet') || cls.includes('wi-fi')) return <Network className="w-5 h-5 text-blue-400" />;
    if (cls.includes('usb') || name.includes('usb')) return <Zap className="w-5 h-5 text-yellow-400" />;
    if (cls.includes('multimedia') || cls.includes('audio') || name.includes('audio')) return <Box className="w-5 h-5 text-pink-400" />;
    if (cls.includes('bridge') || name.includes('root port') || name.includes('switch')) return <Layers className="w-5 h-5 text-tertiary" />;
    return <Box className="w-5 h-5 text-secondary" />;
};

// Helper to get all recursive descendants for "Select Branch"
const getAllDescendantIds = (nodeId: string, map: Map<string, any[]>): string[] => {
    const ids: string[] = [];
    const children = map.get(nodeId) || [];
    for (const child of children) {
        const childId = child.InstanceId || child.instance_id;
        if (childId) {
            ids.push(childId);
            ids.push(...getAllDescendantIds(childId.toLowerCase(), map));
        }
    }
    return ids;
};

// Check if any descendants are selected (for Toggle State)
const areAnyDescendantsSelected = (nodeId: string, map: Map<string, any[]>, selectedIds: string[]): boolean => {
    const descendants = getAllDescendantIds(nodeId, map);
    if (descendants.length === 0) return false;
    return descendants.some(dId => selectedIds.some(sId => sId.toLowerCase() === dId.toLowerCase()));
};

// Helper to find the most "interesting" descendant to bubble up to the Root Port card
const getSignificantDescendant = (nodeId: string, map: Map<string, any[]>): any | null => {
    const lookupId = nodeId.toLowerCase();
    const children = map.get(lookupId) || [];
    for (const child of children) {
        const name = (child.name || child.FriendlyName || '').toLowerCase();
        const cls = (child.class || '').toLowerCase();

        // Priority 1: High-value devices
        if (cls.includes('display') || name.includes('nvidia') || name.includes('radeon')) return child;
        if (cls.includes('nvm') || name.includes('ssd')) return child;
        if (cls.includes('network') || cls.includes('ethernet') || name.includes('wi-fi')) return child;
        if (cls.includes('usb') || name.includes('usb')) return child;
        if (cls.includes('multimedia') || cls.includes('audio')) return child;

        // Recurse
        const deepFound = getSignificantDescendant(child.InstanceId || child.instance_id, map);
        if (deepFound) return deepFound;
    }
    // If no high-value found, return closest endpoint if it's not a bridge?
    return children.length > 0 && !children[0].class?.includes('Bridge') ? children[0] : null;
};

const TreeNode = ({ node, childrenMap, level = 0, isRoot = false, onSelect, onBranchToggle, selectedIds = [] }: { node: any, childrenMap: Map<string, any[]>, level?: number, isRoot?: boolean, onSelect: (node: any, isMulti: boolean) => void, onBranchToggle?: (ids: string[], selected: boolean) => void, selectedIds?: string[] }) => {
    const [expanded, setExpanded] = useState(isRoot); // Expand roots by default
    const nodeId = node.InstanceId || node.instance_id;
    const lookupId = (nodeId || '').toLowerCase(); // Normalize for map lookup

    const children = childrenMap.get(lookupId) || [];
    const hasChildren = children.length > 0;

    // ID Formatter
    const displayId = nodeId ? (nodeId.includes('\\') ? nodeId.split('\\')[1] : nodeId) : '';
    // Case-insensitive check
    const isSelected = selectedIds.some(id => id.toLowerCase() === (nodeId || '').toLowerCase());

    // Context Bubble (only for Roots)
    const significantDescendant = isRoot ? getSignificantDescendant(lookupId, childrenMap) : null;
    const contextName = significantDescendant ? (significantDescendant.name || significantDescendant.FriendlyName) : null;

    // Smart Title Strategy
    // If generic root/bridge name AND we have a descendant, use descendant name as title
    const rawName = node.name || node.Name || node.FriendlyName || 'Unknown Device';
    const primaryLabel = (isRoot && contextName) ? contextName : rawName;
    const secondaryLabel = (isRoot && contextName) ? `Via ${rawName}` : node.class;

    // Handle Branch Toggle
    const handleBranchToggle = (e: React.MouseEvent) => {
        e.stopPropagation();
        const descendantIds = getAllDescendantIds(lookupId, childrenMap);
        const isActive = areAnyDescendantsSelected(lookupId, childrenMap, selectedIds);

        if (isActive) {
            // Toggle OFF: Retract selection from descendants (Keep Self)
            onBranchToggle?.(descendantIds, false);
        } else {
            // Toggle ON: Extend selection to all descendants (Include Self)
            // Ensure self is added too
            onBranchToggle?.([...descendantIds, nodeId], true);
        }
    };

    // Visibility: Show if Self is selected OR any descendant is selected
    const isToggleVisible = isSelected || areAnyDescendantsSelected(lookupId, childrenMap, selectedIds);

    // Auto-Expand if descendant is selected
    React.useEffect(() => {
        if (areAnyDescendantsSelected(lookupId, childrenMap, selectedIds)) {
            setExpanded(true);
        }
    }, [selectedIds, lookupId, childrenMap]);

    // Auto-Scroll if selected
    const elementRef = React.useRef<HTMLDivElement>(null);

    React.useEffect(() => {
        if (isSelected && elementRef.current) {
            elementRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }, [isSelected]);

    return (
        <div className={`transition-all ${level > 0 ? 'ml-3 pl-3 border-l border-white/5' : ''}`}>
            {/* Node Card */}
            <div
                ref={elementRef}
                className={`
                    group relative flex items-center gap-3 p-2 rounded mb-1.5
                    transition-all cursor-pointer border select-none
                    ${isSelected
                        ? 'bg-blue-500/10 border-blue-500 ring-1 ring-blue-500 shadow-[0_0_10px_rgba(59,130,246,0.2)]'
                        : isRoot
                            ? 'bg-surface-2 border-subtle hover:border-secondary/50'
                            : 'bg-surface-1 border-transparent hover:bg-surface-2 hover:border-subtle'}
                    ${node.is_user_disabled ? 'opacity-60 grayscale' : ''}
                `}
                onClick={(e) => { e.stopPropagation(); onSelect(node, e.ctrlKey || e.metaKey); }}
            >
                {/* Expand Toggle (Now Inline Left) */}
                <div
                    className={`
                        w-5 h-5 flex items-center justify-center rounded hover:bg-white/10 text-secondary cursor-pointer
                        transition-transform duration-200 shrink-0
                        ${hasChildren ? 'opacity-100' : 'opacity-0 pointer-events-none'}
                        ${expanded ? 'rotate-90' : ''}
                    `}
                    onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
                >
                    <ChevronRight size={14} />
                </div>

                {/* Visual Anchor / Icon */}
                <div className="flex-shrink-0 opacity-80 group-hover:opacity-100 transition-opacity p-1.5 bg-black/20 rounded-md">
                    {getDeviceIcon(isRoot && significantDescendant ? significantDescendant : node)}
                </div>

                {/* Content Container */}
                <div className="flex-1 min-w-0 flex flex-col justify-center">
                    <div className="flex items-center justify-between gap-2">
                        <span className={`text-sm font-bold truncate ${isSelected ? 'text-blue-400' : 'text-primary'}`}>
                            {primaryLabel}
                        </span>

                        {/* Right Side Actions */}
                        <div className="flex items-center gap-2">
                            {/* Recursive Toggle Switch */}
                            {hasChildren && (
                                <div
                                    className={`
                                        flex items-center gap-1.5 px-1.5 py-0.5 rounded cursor-pointer transition-all
                                        ${isToggleVisible ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}
                                        ${areAnyDescendantsSelected(lookupId, childrenMap, selectedIds)
                                            ? 'bg-blue-500/10 text-blue-400 hover:bg-blue-500/20'
                                            : 'hover:bg-surface-2 text-secondary'}
                                    `}
                                    onClick={handleBranchToggle}
                                    title={areAnyDescendantsSelected(lookupId, childrenMap, selectedIds) ? "Retract Branch" : "Extend Branch"}
                                >
                                    <span className="text-[9px] font-bold uppercase tracking-wider">Tree</span>
                                    {areAnyDescendantsSelected(lookupId, childrenMap, selectedIds) ? (
                                        <ToggleRight size={16} className="text-blue-500" />
                                    ) : (
                                        <ToggleLeft size={16} />
                                    )}
                                </div>
                            )}

                            {/* BDF Address Tag (No BG) */}
                            {displayId && <span className="text-[10px] font-mono text-tertiary opacity-60 ml-1">{displayId}</span>}

                            {/* Status Badge */}
                            {(node.is_user_disabled || node.cm_prob === 22) ? (
                                <span className="text-[10px] font-bold uppercase bg-orange-500/20 text-orange-500 px-2 py-0.5 rounded-full border border-orange-500/20">
                                    Disabled
                                </span>
                            ) : (
                                <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${node.status === 'OK' ? 'bg-green-500/10 text-green-500 border-green-500/20' : 'bg-red-500/10 text-red-500 border-red-500/20'}`}>
                                    {node.status === 'Error' ? 'Error' : 'Active'}
                                </span>
                            )}
                        </div>
                    </div>

                    {/* Meta Row */}
                    <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[10px] text-secondary truncate max-w-[300px]">{secondaryLabel}</span>

                        {/* Root Port Link Indicator */}
                        {isRoot && contextName && (
                            <span className="text-[10px] text-tertiary flex items-center gap-1">
                                <ChevronRight size={10} />
                                Link
                            </span>
                        )}

                        {/* Physics Hints */}
                        {node.upstream_chain && node.upstream_chain.length > 0 && (
                            <span className="text-[9px] text-blue-500/50 font-mono hidden group-hover:block ml-auto">
                                Depth: {node.upstream_chain.length}
                            </span>
                        )}
                    </div>
                </div>
            </div>

            {/* Recursion */}
            {expanded && hasChildren && (
                <div className="mt-1">
                    {children.map((child, i) => (
                        <TreeNode key={i} node={child} childrenMap={childrenMap} level={level + 1} onSelect={onSelect} onBranchToggle={onBranchToggle} selectedIds={selectedIds} />
                    ))}
                </div>
            )}
        </div>
    );
};

export const TopologyTree = ({ roots, endpoints, onNodeSelect, onBranchToggle, selectedIds = [] }: { roots: any[], endpoints: any[], onNodeSelect?: (node: any, isMulti: boolean) => void, onBranchToggle?: (ids: string[], selected: boolean) => void, selectedIds?: string[] }) => {
    // Build Parent->Children Map (Robust: Normalized Case & Property Fallbacks)
    const childrenMap = useMemo(() => {
        const map = new Map<string, any[]>();
        endpoints.forEach(ep => {
            let rawParent = ep.parent_instance_id || ep.ParentInstanceId;

            // Fallback: Derive parent from upstream_chain (Index 1 is parent, Index 0 is self)
            if (!rawParent && ep.upstream_chain && ep.upstream_chain.length > 1) {
                rawParent = ep.upstream_chain[1];
            }

            if (rawParent) {
                const parentKey = rawParent.toLowerCase();
                if (!map.has(parentKey)) map.set(parentKey, []);
                map.get(parentKey)?.push(ep);
            }
        });
        return map;
    }, [roots, endpoints]);

    return (
        <div className="p-2 select-none space-y-1">
            {roots.length === 0 && <div className="text-xs text-secondary italic p-4">No Root Ports found.</div>}
            {roots.map((root, i) => (
                <TreeNode key={i} node={root} childrenMap={childrenMap} isRoot={true} onSelect={(n, m) => onNodeSelect?.(n, m)} onBranchToggle={onBranchToggle} selectedIds={selectedIds} />
            ))}
        </div>
    );
};
