import React, { useState, useEffect, useRef } from 'react';
import { Layers, Copy, Trash2, Cpu, Activity, Settings, List, ChevronUp, ChevronDown, SlidersHorizontal, Loader2 } from 'lucide-react';
import { useContextStore, ContextItem } from './ContextStore';
import { fetchPreCrashContext } from '@/lib/api';

// --- Context Item Row Component ---
// Extracted to handle per-item local state and effects (debounce)
const ContextItemRow: React.FC<{ item: ContextItem }> = ({ item }) => {
    const removeItem = useContextStore(state => state.removeItem);

    // Custom updateItem function to ensure it's stable and updates the store correctly
    const storeUpdateItem = (id: string, updates: any) => {
        useContextStore.setState(prev => ({
            items: prev.items.map(i => i.id === id ? { ...i, ...updates } : i)
        }));
    };

    const [expanded, setExpanded] = useState(false);
    const [loading, setLoading] = useState(false);

    // Local count state for input to allow immediate typing
    const [localCount, setLocalCount] = useState(item.preCrashConfig?.count || 50);

    // Ref to track if we should debounce fetch (skip on initial render)
    const isFirstRun = useRef(true);

    // Sync local count if store changes externally (unlikely but good practice)
    useEffect(() => {
        if (item.preCrashConfig?.count && item.preCrashConfig.count !== localCount) {
            setLocalCount(item.preCrashConfig.count);
        }
    }, [item.preCrashConfig?.count]);


    // Helper: Execute Fetch
    const executeFetch = async (countVal: number) => {
        const timestamp = item.data.TimeCreated;
        if (!timestamp) return;

        setLoading(true);
        try {
            const logs = await fetchPreCrashContext(timestamp, countVal);
            storeUpdateItem(item.id, {
                preCrashConfig: {
                    ...(item.preCrashConfig || {}),
                    enabled: true,
                    count: countVal,
                    fetched: true,
                    logs
                }
            });
        } catch (e) {
            console.error("Failed to fetch pre-crash context", e);
            // Optionally revert enabled state or show error message
            storeUpdateItem(item.id, {
                preCrashConfig: {
                    ...(item.preCrashConfig || {}),
                    enabled: true, // Keep enabled, but indicate fetch failed
                    fetched: false,
                    logs: [] // Clear logs on error
                }
            });
        } finally {
            setLoading(false);
        }
    };

    // Debounce Effect for Count Change
    useEffect(() => {
        // Only run if enabled and NOT first run (prevent auto-fetch on mount unless intended?)
        // Actually, on mount we don't fetch. Only if we CHANGE the count.
        // Wait, what if we just toggled it ON? 
        // Toggle logic handles the immediate fetch. This is for UPDATES to count.

        if (isFirstRun.current) {
            isFirstRun.current = false;
            return;
        }

        if (item.preCrashConfig?.enabled) {
            const timer = setTimeout(() => {
                // Only fetch if localCount is different from the stored count
                // This prevents unnecessary fetches if the store updates localCount
                if (localCount !== item.preCrashConfig?.count) {
                    executeFetch(localCount);
                }
            }, 800); // 800ms debounce
            return () => clearTimeout(timer);
        }
    }, [localCount, item.preCrashConfig?.enabled, item.preCrashConfig?.count]);


    const handleToggle = (checked: boolean) => {
        if (checked) {
            // Enable -> Fetch immediately
            storeUpdateItem(item.id, {
                preCrashConfig: { ...(item.preCrashConfig || {}), enabled: true, count: localCount, fetched: false }
            });
            executeFetch(localCount);
        } else {
            // Disable
            storeUpdateItem(item.id, {
                preCrashConfig: { ...(item.preCrashConfig || {}), enabled: false }
            });
        }
    };

    // Is this a toggleable event type?
    const isEvent = item.type === 'event' || item.type === 'log';
    const isBatch = item.type === 'event_batch';

    return (
        <div className="bg-surface-1 border border-subtle rounded-lg flex flex-col group transition-all overflow-hidden mb-2">

            {/* Main Row */}
            <div className="flex items-stretch p-3 gap-3">
                {/* Icon */}
                <div className="mt-0.5 shrink-0">
                    {item.type === 'hardware' ? <Cpu className="w-4 h-4 text-purple-400" /> :
                        item.type === 'event_batch' ? <Layers className="w-4 h-4 text-teal-400" /> :
                            item.type === 'event' ? <Activity className="w-4 h-4 text-orange-400" /> :
                                <Layers className="w-4 h-4 text-blue-400" />}
                </div>

                {/* Content: Title & Subtext */}
                <div className="flex-1 min-w-0 flex flex-col justify-center gap-0.5">
                    <div className="font-medium text-sm truncate text-primary">{item.title}</div>

                    <div className="text-xs font-mono truncate h-5 flex items-center">
                        {isEvent ? (
                            // Helper Label for Pre-Crash
                            <div className="flex items-center gap-2">
                                <span className="text-secondary opacity-60">
                                    {(item.data.TimeCreated || '').split('T')[1]?.split('.')[0] || 'Unknown Time'}
                                </span>
                                {item.preCrashConfig?.enabled ? (
                                    <button
                                        onClick={(e) => { e.stopPropagation(); handleToggle(false); }}
                                        className="flex items-center gap-1.5 px-1.5 py-0.5 rounded bg-blue-500/10 hover:bg-red-500/10 border border-blue-500/20 hover:border-red-500/20 transition-colors group/badge"
                                        title="Click to Disable"
                                    >
                                        <span className="w-1.5 h-1.5 rounded-full bg-blue-400 group-hover/badge:bg-red-400 shadow-[0_0_4px_rgba(96,165,250,0.6)]"></span>
                                        <span className="text-[10px] text-blue-300 group-hover/badge:text-red-300 font-sans font-medium">
                                            Pre-Crash: {item.preCrashConfig.logs?.length || 0} fetched
                                        </span>
                                    </button>
                                ) : (
                                    <button
                                        onClick={(e) => { e.stopPropagation(); handleToggle(true); }}
                                        className="text-[10px] text-tertiary hover:text-secondary px-1.5 py-0.5 rounded hover:bg-surface-3 transition-colors font-sans"
                                        title="Click to Enable Pre-Crash Context"
                                    >
                                        + Pre-Crash Context
                                    </button>
                                )}
                            </div>
                        ) : isBatch ? (
                            <span className="text-secondary">
                                {item.verbosity === 'summary' ? 'TABLE' : 'FULL'} • {(item.data as any[]).length} events
                            </span>
                        ) : (
                            <span className="text-secondary opacity-50">
                                {JSON.stringify(item.data || {}).slice(0, 40)}...
                            </span>
                        )}
                    </div>
                </div>

                {/* Actions Column: Full Height */}
                <div className="hidden group-hover:flex items-stretch pl-1 ml-1 self-stretch animate-in fade-in slide-in-from-right-2 duration-200">

                    {isEvent && (
                        <button
                            onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
                            className={`px-2 flex items-center justify-center transition-colors cursor-pointer ${expanded ? 'bg-surface-3 text-primary' : 'text-tertiary hover:text-primary hover:bg-surface-2'}`}
                            title="Configure Context"
                        >
                            <SlidersHorizontal className="w-4 h-4" />
                        </button>
                    )}

                    {isBatch && (
                        <button
                            onClick={() => storeUpdateItem(item.id, { verbosity: (item.verbosity === 'summary' || !item.verbosity) ? 'full' : 'summary' })}
                            className={`px-2 flex items-center justify-center transition-colors cursor-pointer ${item.verbosity === 'summary' ? 'text-teal-400' : 'text-tertiary hover:text-primary'}`}
                            title="Toggle Summary/Full"
                        >
                            <Settings className="w-4 h-4" />
                        </button>
                    )}

                    <button
                        onClick={() => removeItem(item.id)}
                        className="px-2 flex items-center justify-center text-tertiary hover:text-red-400 hover:bg-red-500/10 transition-colors cursor-pointer"
                        title="Remove Item"
                    >
                        <Trash2 className="w-4 h-4" />
                    </button>
                </div>
            </div>

            {/* Expanded Panel */}
            {expanded && isEvent && (
                <div className="border-t border-subtle bg-surface-2/50 p-3 animate-in slide-in-from-top-1 fade-in duration-200">
                    <div className="flex items-center justify-between mb-4">
                        <div className="flex flex-col">
                            <span className="text-xs font-bold text-primary">Pre-Crash Context</span>
                            <span className="text-[10px] text-tertiary">Fetch events prior to this timestamp</span>
                        </div>
                        {/* Toggle */}
                        <label className="relative inline-flex items-center cursor-pointer">
                            <input
                                type="checkbox"
                                className="sr-only peer"
                                checked={!!item.preCrashConfig?.enabled}
                                onChange={(e) => handleToggle(e.target.checked)}
                            />
                            <div className="w-9 h-5 bg-surface-3 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600"></div>
                        </label>
                    </div>

                    {item.preCrashConfig?.enabled && (
                        <div className="flex flex-col gap-4">
                            {/* Mode & Count Row */}
                            <div className="flex items-end gap-3">
                                <div className="flex-1">
                                    <label className="text-[10px] text-secondary uppercase font-bold mb-1.5 block">Log Count</label>
                                    <div className="flex items-center">
                                        <input
                                            type="number"
                                            min="10"
                                            max="500"
                                            className="w-full bg-surface-1 border border-subtle rounded-l px-3 py-1.5 text-xs text-primary focus:border-blue-500 outline-none transition-all placeholder:text-tertiary appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                                            value={localCount}
                                            onChange={(e) => setLocalCount(parseInt(e.target.value) || 10)}
                                            placeholder="50"
                                        />
                                        {/* Status Badge Attached to Input */}
                                        <div className={`
                                            h-[30px] px-3 flex items-center justify-center border-y border-r border-subtle rounded-r text-[10px] font-medium min-w-[80px]
                                            ${loading
                                                ? 'bg-blue-500/10 text-blue-400 border-blue-500/20'
                                                : item.preCrashConfig?.fetched
                                                    ? 'bg-green-500/10 text-green-400 border-green-500/20'
                                                    : 'bg-surface-3 text-secondary'}
                                        `}>
                                            {loading ? (
                                                <Loader2 className="w-3 h-3 animate-spin" />
                                            ) : (
                                                <span>{item.preCrashConfig?.logs ? `${item.preCrashConfig.logs.length} fetched` : 'Ready'}</span>
                                            )}
                                        </div>
                                    </div>
                                </div>

                                <div className="flex flex-col shrink-0">
                                    <label className="text-[10px] text-secondary uppercase font-bold mb-1.5 block">Output Format</label>
                                    <div className="flex items-center bg-surface-1 border border-subtle rounded overflow-hidden p-0.5 h-[30px]">
                                        <button
                                            onClick={() => storeUpdateItem(item.id, { preCrashConfig: { ...item.preCrashConfig, verbosity: 'summary' } })}
                                            className={`px-3 h-full rounded-sm text-[10px] font-medium transition-colors flex items-center gap-1.5 ${(!item.preCrashConfig?.verbosity || item.preCrashConfig.verbosity === 'summary') ? 'bg-surface-3 text-primary shadow-sm' : 'text-tertiary hover:text-secondary'}`}
                                            title="Summary Table"
                                        >
                                            <List className="w-3 h-3" /> Table
                                        </button>
                                        <button
                                            onClick={() => storeUpdateItem(item.id, { preCrashConfig: { ...item.preCrashConfig, verbosity: 'full' } })}
                                            className={`px-3 h-full rounded-sm text-[10px] font-medium transition-colors flex items-center gap-1.5 ${item.preCrashConfig?.verbosity === 'full' ? 'bg-surface-3 text-primary shadow-sm' : 'text-tertiary hover:text-secondary'}`}
                                            title="Full Log JSON"
                                        >
                                            <Settings className="w-3 h-3" /> Full Logs
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

// --- Main Context Composer ---

export const ContextComposer: React.FC = () => {
    const {
        items, isOpen, setOpen, clearContext,
        systemPromptConfig, updateSystemPromptConfig,
        sections, toggleSection, generateSystemPrompt,
        prompts, activePromptId, setActivePrompt
    } = useContextStore();

    const getActivePromptContent = () => {
        const p = prompts.find(x => x.id === activePromptId);
        return p ? p.content : '';
    };

    const [activeTab, setActiveTab] = useState<'stack' | 'sections' | 'prompt'>('stack');
    const [copied, setCopied] = useState(false);

    if (!isOpen) return null;

    const handleCopy = () => {
        const text = generateSystemPrompt();
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="fixed bottom-24 right-6 w-[450px] h-[600px] bg-surface-1 border border-subtle rounded-xl shadow-2xl z-50 flex flex-col overflow-hidden animate-in slide-in-from-bottom-6 fade-in duration-300">
            {/* Header */}
            <div className="h-12 bg-surface-2 border-b border-subtle flex items-center justify-between px-4 shrink-0">
                <div className="flex items-center gap-2">
                    <Layers className="w-4 h-4 text-blue-500" />
                    <span className="font-bold text-sm">Context Composer</span>
                    <span className="bg-surface-3 text-secondary text-xs px-2 py-0.5 rounded-full">{items.length} Items</span>
                </div>
                <button onClick={() => setOpen(false)} className="text-tertiary hover:text-primary">
                    <ChevronDown className="w-5 h-5" />
                </button>
            </div>

            {/* Tabs */}
            <div className="flex items-center border-b border-subtle bg-surface-1">
                {[
                    { id: 'stack', label: 'Stack', icon: List },
                    { id: 'sections', label: 'Globals', icon: Cpu },
                    { id: 'prompt', label: 'System Prompt', icon: Settings },
                ].map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id as any)}
                        className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm font-medium transition-colors relative
                            ${activeTab === tab.id ? 'text-primary bg-surface-active' : 'text-secondary hover:bg-surface-2 hover:text-primary'}
                        `}
                    >
                        <tab.icon className="w-3.5 h-3.5" />
                        {tab.label}
                        {activeTab === tab.id && <div className="absolute bottom-0 left-0 w-full h-0.5 bg-blue-500"></div>}
                    </button>
                ))}
            </div>

            {/* Content Area */}
            <div className="flex-1 overflow-y-auto custom-scrollbar bg-bg-app pl-4 py-4 pr-2 [scrollbar-gutter:stable]">

                {/* 1. Context Stack List */}
                {activeTab === 'stack' && (
                    <div className="flex flex-col">
                        {items.length === 0 ? (
                            <div className="text-center py-12 text-tertiary text-sm">
                                <List className="w-8 h-8 mx-auto mb-2 opacity-20" />
                                <p>Stack is empty.</p>
                                <p className="text-xs">Select items in the dashboard to add them here.</p>
                            </div>
                        ) : (
                            items.map(item => (
                                <ContextItemRow key={item.id} item={item} />
                            ))
                        )}
                        {items.length > 0 && (
                            <button onClick={clearContext} className="mt-4 text-xs text-red-400 hover:text-red-300 flex items-center gap-1 justify-center w-full py-2 border border-dashed border-red-500/20 rounded hover:bg-red-500/10">
                                <Trash2 className="w-3 h-3" /> Clear Stack
                            </button>
                        )}
                    </div>
                )}

                {/* 2. Global Sections Toggles */}
                {activeTab === 'sections' && (
                    <div className="flex flex-col gap-4">
                        <p className="text-xs text-secondary">Global sections are automatically retrieved and prepended to your context stack.</p>

                        <div className="bg-surface-1 border border-subtle rounded-lg overflow-hidden flex flex-col">
                            {/* Hardware */}
                            <label className="flex items-center justify-between p-4 cursor-pointer hover:bg-surface-2 transition-colors">
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-purple-500/10 rounded-lg"><Cpu className="w-5 h-5 text-purple-500" /></div>
                                    <div>
                                        <div className="font-medium text-sm">Hardware Specifications</div>
                                        <div className="text-xs text-secondary">CPU, RAM, Uptime, Platform</div>
                                    </div>
                                </div>
                                <div className="relative inline-flex items-center">
                                    <input type="checkbox" checked={sections.hardware} onChange={() => toggleSection('hardware')} className="sr-only peer" />
                                    <div className="w-9 h-5 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600"></div>
                                </div>
                            </label>

                            {/* Drivers */}
                            <label className="flex items-center justify-between p-4 cursor-pointer hover:bg-surface-2 transition-colors">
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-pink-500/10 rounded-lg"><Activity className="w-5 h-5 text-pink-500" /></div>
                                    <div>
                                        <div className="font-medium text-sm">Driver Changes</div>
                                        <div className="text-xs text-secondary">Recent PnP driver installs (Last 14 days)</div>
                                    </div>
                                </div>
                                <div className="relative inline-flex items-center">
                                    <input type="checkbox" checked={sections.drivers} onChange={() => toggleSection('drivers')} className="sr-only peer" />
                                    <div className="w-9 h-5 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600"></div>
                                </div>
                            </label>

                            {/* PCIe Fabric */}
                            <label className="flex items-center justify-between p-4 cursor-pointer hover:bg-surface-2 transition-colors">
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-blue-500/10 rounded-lg"><Settings className="w-5 h-5 text-blue-500" /></div>
                                    <div>
                                        <div className="font-medium text-sm">PCIe Fabric & Topology</div>
                                        <div className="text-xs text-secondary">Bus hierarchy, link width/speeds, latency clusters</div>
                                    </div>
                                </div>
                                <div className="relative inline-flex items-center">
                                    <input type="checkbox" checked={sections.pcie} onChange={() => toggleSection('pcie')} className="sr-only peer" />
                                    <div className="w-9 h-5 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600"></div>
                                </div>
                            </label>
                        </div>
                    </div>
                )}

                {/* 3. System Prompt Tuning */}
                {activeTab === 'prompt' && (
                    <div className="flex flex-col gap-4 h-full">
                        <div className="flex items-center justify-between">
                            <span className="text-sm font-medium">Enable System Prompt</span>
                            <div
                                onClick={() => updateSystemPromptConfig({ enabled: !systemPromptConfig.enabled })}
                                className={`w-10 h-5 rounded-full cursor-pointer relative transition-colors ${systemPromptConfig.enabled ? 'bg-blue-600' : 'bg-surface-3'}`}
                            >
                                <div className={`absolute top-1 w-3 h-3 bg-white rounded-full transition-all ${systemPromptConfig.enabled ? 'left-6' : 'left-1'}`}></div>
                            </div>
                        </div>

                        {systemPromptConfig.enabled && (
                            <>
                                <div className="flex flex-col gap-2">
                                    <label className="text-xs text-secondary font-medium uppercase">Active Archetype</label>
                                    <select
                                        value={activePromptId}
                                        onChange={(e) => setActivePrompt(e.target.value)}
                                        className="bg-surface-1 border border-subtle rounded-md p-2 text-sm text-primary focus:outline-none focus:border-blue-500"
                                    >
                                        {prompts.map(p => (
                                            <option key={p.id} value={p.id}>{p.name}</option>
                                        ))}
                                    </select>
                                    <p className="text-xs text-secondary">
                                        Select specialized archetypes from the <span className="font-bold">Prompt Gallery</span> view.
                                    </p>
                                </div>

                                <div className="flex flex-col gap-2 flex-1">
                                    <label className="text-xs text-secondary font-medium uppercase">Prompt Preview (Editable)</label>
                                    <textarea
                                        value={systemPromptConfig.customText || getActivePromptContent()}
                                        placeholder="Customize the active prompt here (overrides archetype)..."
                                        onChange={(e) => updateSystemPromptConfig({ customText: e.target.value })}
                                        className="flex-1 bg-surface-1 border border-subtle rounded-md p-3 text-xs font-mono text-secondary resize-none focus:outline-none focus:border-blue-500"
                                    ></textarea>
                                </div>
                            </>
                        )}
                    </div>
                )}
            </div>

            {/* Footer */}
            <div className="p-4 bg-surface-2 border-t border-subtle">
                <button
                    onClick={handleCopy}
                    className={`
                        w-full py-3 rounded-lg font-bold text-sm flex items-center justify-center gap-2 transition-all shadow-lg
                        ${copied ? 'bg-emerald-600 text-white' : 'bg-blue-600 hover:bg-blue-500 text-white hover:shadow-blue-500/20'}
                    `}
                >
                    {copied ? (
                        <><span>Copied to Clipboard!</span></>
                    ) : (
                        <><Copy className="w-4 h-4" /><span>Copy Context for AI</span></>
                    )}
                </button>
            </div>
        </div>
    );
};
