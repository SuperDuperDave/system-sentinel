"use client";

import React, { useEffect, useState } from 'react';
import { Sidebar } from './Sidebar';
import { EventList } from './EventList';
import { Inspector, InspectorSection, InspectorItem, CollapsibleCode } from './Inspector';
import { fetchSystemLogs, fetchWheaLogs, createCapturePack } from '@/lib/api';
import { SystemLog, WheaLog } from '@/lib/types';
import { Search, Loader2, Shield, Activity } from 'lucide-react';


// Augmented types for internal use
type AugmentedEvent = (SystemLog | WheaLog | any) & { type: string, uniqueId: string };

import { DashboardGrid } from './DashboardGrid';
import { DashboardControls } from './DashboardControls';
import { DriversView } from './views/DriversView';
import { HardwareView } from './views/HardwareView';
import { PromptGalleryView } from './views/PromptGalleryView';
import { OverviewView, PCIeView, PowerView, MemoryView, ForensicSignalsView, CpuView, GpuView, MotherboardView, StorageView, NetworkView } from './views/DeepDiagnostics';
import { ContextComposer } from './context/ContextComposer';
import { WheaStormView } from './views/WheaStormView';
import { SmartContextControl } from './context/SmartContextControl';
import { useContextStore } from './context/ContextStore';
import { useSystemStore, useDashboardStore } from '@/lib/store';
import { useSelectionStore } from '@/lib/selectionStore';
import { SentinelEvent } from '@/lib/types';
import { parseDate, formatDateTime } from '@/lib/dateUtils';


export const Dashboard: React.FC = () => {
    // Local state for legacy logs only (if needed) across switches
    const [legacyLogs, setLegacyLogs] = useState<SystemLog[]>([]);
    const [legacyWhea, setLegacyWhea] = useState<WheaLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedEvent, setSelectedEvent] = useState<AugmentedEvent | null>(null);

    // Store Hooks
    const { events: liveEvents, liveFeedEnabled, setStreamStatus, addEvent, setHeartbeat, activeView, setActiveView, inspectableItem, setInspectableItem } = useSystemStore();
    const { selectedIds } = useSelectionStore();

    // Clear selection when navigating views
    useEffect(() => {
        // We use getState() to avoid subscribing to the store here if we don't need to render based on selection
        // But importing the hook is cleaner
        const clear = useSelectionStore.getState().clearSelection;
        clear();
        setInspectableItem(null); // Clear inspector for new view
    }, [activeView]);

    // Sync inspectableItem (from DeepDiagnostics) to local selectedEvent (for Inspector)
    useEffect(() => {
        if (inspectableItem) {
            setSelectedEvent(inspectableItem);
        }
    }, [inspectableItem]);

    useEffect(() => {
        const loadData = async () => {
            setLoading(true);
            try {
                const [sys, whea] = await Promise.all([
                    fetchSystemLogs(20),
                    fetchWheaLogs(20)
                ]);
                setLegacyLogs(sys);
                setLegacyWhea(whea);
            } catch (e) {
                console.error("Failed to load initial logs", e);
            } finally {
                setLoading(false);
            }
        };
        // Only load if we are in a view that needs it? Or just load once.
        loadData();
    }, []);

    // SSE Connection Manager
    useEffect(() => {
        let evtSource: EventSource | null = null;

        if (liveFeedEnabled) {
            setStreamStatus('reconnecting');
            evtSource = new EventSource(`${useSystemStore.getState().apiBaseUrl}/api/events/stream`);

            evtSource.onopen = () => setStreamStatus('connected');

            evtSource.onmessage = (e) => {
                try {
                    const data = JSON.parse(e.data);
                    if (data.type === 'heartbeat') {
                        setHeartbeat(data.ts);
                    } else if (data.recordId || data.eventId) {
                        addEvent(data); // data matches SentinelEvent schema
                    }
                } catch (error) {
                    console.error("SSE Parse Error", error);
                }
            };

            evtSource.onerror = (err) => {
                console.error("SSE Error", err);
                setStreamStatus('reconnecting');
            };
        } else {
            setStreamStatus('disconnected');
        }

        return () => {
            if (evtSource) {
                evtSource.close();
            }
        };
    }, [liveFeedEnabled]);

    // Merge Live Events into Legacy format for the List View
    const combinedSystemLogs = React.useMemo(() => {
        return [
            ...liveEvents.filter(e => e.logName === 'System' || !e.logName).map((e, idx) => ({
                TimeCreated: e.timeCreated,
                Id: e.eventId,
                LevelDisplayName: e.level,
                Message: e.message,
                ProviderName: e.provider,
                _uuid: e.uniqueId || `live-${e.recordId}-${e.timeCreated}-${idx}`,
                Properties: e.raw ? { Raw: e.raw } : {}
            })),
            ...legacyLogs.map((log, idx) => ({
                ...log,
                // Assign a persistent computed UUID if not present
                _uuid: (log as any)._uuid || `sys-${log.Id}-${log.TimeCreated}-${idx}`
            }))
        ] as SystemLog[];
    }, [liveEvents, legacyLogs]);


    // Selection Logic
    const handleSelect = (item: SystemLog | WheaLog | null) => {
        if (!item) {
            setSelectedEvent(null);
            return;
        }
        // Item coming from EventList ALREADY has the correct uniqueId with index
        const isWhea = 'RawData' in item || (item as any).DecodedCPER;
        const type = isWhea ? 'whea' : 'system';

        // Use the ID explicitly passed from the list component, which guarantees uniqueness
        const uniqueId = (item as any).uniqueId || (item as any)._uuid || (isWhea
            ? `whea-${item.Id}-${item.TimeCreated}`
            : `sys-${item.Id}-${item.TimeCreated}`);

        setSelectedEvent({ ...item, type, uniqueId } as AugmentedEvent);
    };

    // Content Renderer
    const renderContent = () => {
        switch (activeView) {
            case 'overview':
                return (
                    <div className="relative w-full h-full overflow-y-auto custom-scrollbar">
                        <DashboardGrid />
                        <DashboardControls />
                    </div>
                );
            case 'drivers':
                return <DriversView />;
            case 'diagnostics':
                return <OverviewView />;
            // New V2.1 Domain Pages
            case 'dd_overview': return <OverviewView />;
            case 'dd_pcie': return <PCIeView />;
            case 'dd_power': return <PowerView />;
            case 'dd_memory': return <MemoryView />;
            case 'forensic_signals': return <ForensicSignalsView />;
            case 'hardware': return <HardwareView />;
            case 'cpu': return <CpuView />;
            case 'gpu': return <GpuView />;
            case 'board': return <MotherboardView />;
            case 'storage': return <StorageView />;
            case 'network': return <NetworkView />;
            case 'gallery':
                return <PromptGalleryView />;


            case 'events':
                return (
                    <EventList
                        systemEvents={combinedSystemLogs}
                        wheaEvents={legacyWhea} // Keep loading this for now even if hidden? or maybe optimize later
                        filter={'system'} // If events view, only system? Dashboard default implementation covers this
                        onSelect={handleSelect}
                        selectedId={selectedEvent?.uniqueId || null}
                    />
                );
            case 'whea_corrected':
                return <WheaStormView />;
            case 'whea':
            default:
                return (
                    <EventList
                        systemEvents={combinedSystemLogs}
                        wheaEvents={legacyWhea}
                        filter={activeView === 'whea' ? 'whea' : activeView === 'events' ? 'system' : 'all'}
                        onSelect={handleSelect}
                        selectedId={selectedEvent?.uniqueId || null}
                    />
                );

            case 'dumps':
                // Filter for Crash-related events (BugCheck, Critical errors)
                // This prevents "Crash Dumps" from looking identical to "System Events"
                const crashEvents = combinedSystemLogs.filter(e => {
                    const isBugCheck = e.ProviderName === 'Microsoft-Windows-WER-SystemErrorReporting' || e.Id === 1001;
                    const isCritical = e.LevelDisplayName === 'Critical';
                    return isBugCheck || isCritical;
                });

                return (
                    <EventList
                        systemEvents={crashEvents}
                        wheaEvents={[]} // Usually WHEA are hardware errors, distinct from crash dumps, but can be related. Let's keep them separate for now.
                        filter={'system'}
                        onSelect={handleSelect}
                        selectedId={selectedEvent?.uniqueId || null}
                    />
                );
        }
    };




    // Determine items available for SmartContextControl based on active view
    let availableForContext: any[] = combinedSystemLogs; // Default

    if (activeView === 'hardware') {
        const snap = useSystemStore.getState().hardwareSnapshot;
        availableForContext = snap ? [snap] : [];
    } else if (activeView === 'dumps') {
        availableForContext = combinedSystemLogs.filter(e => {
            const isBugCheck = e.ProviderName === 'Microsoft-Windows-WER-SystemErrorReporting' || e.Id === 1001;
            const isCritical = e.LevelDisplayName === 'Critical';
            return isBugCheck || isCritical;
        });
    } else if (activeView === 'whea') {
        // Must map WHEA events to have uniqueId matching EventList logic
        // Use index to ensure stability
        availableForContext = legacyWhea.map((e, idx) => ({
            ...e,
            uniqueId: (e as any)._uuid || `whea-${e.Id}-${e.TimeCreated}-${idx}`,
            // Ensure properties expected by SmartContextControl exist
            ProviderName: 'Microsoft-Windows-WHEA-Logger',
            LevelDisplayName: 'Critical'
        }));
    } else if (activeView === 'dd_pcie') {
        // V2 PCIe Selection Mapping
        const report = useSystemStore.getState().diagnosticReport;

        // If items are selected, populate Tray with those specific items
        if (selectedIds.size > 0 && report?.domains?.pcie) {
            const endpoints = report.domains.pcie.raw?.endpoints || [];
            const roots = report.domains.pcie.raw?.roots || [];
            const allNodes = [...roots, ...endpoints];

            // Filter by selection
            availableForContext = allNodes
                .filter(n => selectedIds.has(n.InstanceId || n.instance_id))
                .map(n => ({
                    ...n,
                    uniqueId: n.InstanceId || n.instance_id,
                    title: n.name || n.FriendlyName,
                    description: n.class,
                    type: 'hardware' // Mapped for Tray
                }));
        } else {
            // Fallback: Show full report as single item? Or nothing?
            // User prefers explicit adding.
            availableForContext = report ? [report] : [];
        }
    } else if (activeView === 'dd_memory') {
        const report = useSystemStore.getState().diagnosticReport;
        const mem = report?.domains?.memory || report?.memory;

        // Flatten Memory Ledger events for global context availability
        let items: any[] = [];
        if (mem?.ledger?.events) {
            items = mem.ledger.events.map((e: any) => ({
                ...e,
                uniqueId: (e as any)._uuid || (e.Type === 'whea' ? `whea-${e.Id}-${e.TimeCreated}` : `sys-${e.Id}-${e.TimeCreated}`),
                title: e.Type === 'whea' ? 'Hardware Error' : 'System Event',
                description: e.Message,
                // Ensure type matches generic inspector or specific mapping?
                type: e.Type === 'whea' ? 'whea' : 'system'
            }));
        }
        availableForContext = items;
    } else if (activeView === 'forensic_signals') {
        const report = useSystemStore.getState().diagnosticReport;
        const signals = report?.domains?.forensic_signals;

        let items: any[] = [];
        if (signals?.timeline?.events) {
            items = signals.timeline.events.map((e: any, idx: number) => ({
                ...e,
                uniqueId: `timeline-${e.ProviderName}-${e.Id}-${e.TimeCreated}-${idx}`, // Stable ID matching View (Global Index)
                title: `Event ${e.Id} - ${e.ProviderName}`,
                description: e.Message,
                type: 'evidence', // Matches ForensicSignalsView add logic
                evidenceClass: 'raw',
                rank: 2,
                sourceId: `timeline-${e.ProviderName}-${e.Id}-${e.TimeCreated}-${idx}`
            }));
        }
        availableForContext = items;
    } else if (activeView === 'diagnostics' || activeView.startsWith('dd_')) {
        const report = useSystemStore.getState().diagnosticReport;
        availableForContext = Array.isArray(report) ? report : (report ? [report] : []);
    }

    return (
        <div className="flex h-screen w-full bg-app text-primary overflow-hidden font-sans">
            <Sidebar />

            <div className="flex-1 flex flex-col min-w-0 bg-bg-app">
                <header className="h-14 border-b border-subtle flex items-center px-4 justify-between shrink-0 bg-bg-app/80 backdrop-blur z-10">
                    <div className="flex items-center gap-3 w-full max-w-md">
                        <Search className="w-4 h-4 text-tertiary" />
                        <input
                            type="text"
                            placeholder="Search events, errors, IDs..."
                            className="bg-transparent border-none outline-none text-sm w-full placeholder:text-tertiary text-primary"
                        />
                    </div>
                </header>

                <main className="flex-1 relative flex overflow-hidden">
                    {renderContent()}

                    {selectedEvent && (

                        <Inspector
                            title={selectedEvent.panelTitle || (selectedEvent.type === 'hardware' ? "Hardware Details" : "Event Details")}
                            onClose={() => setSelectedEvent(null)}
                            onCopy={() => navigator.clipboard.writeText(JSON.stringify(selectedEvent, null, 2))}
                            eventData={selectedEvent}
                        >


                            <div className="mb-6">
                                <h2 className="text-lg font-bold text-primary mb-1">
                                    {selectedEvent.type === 'whea' ? 'WHEA Hardware Error' :
                                        selectedEvent.type === 'hardware' ? (selectedEvent.name || selectedEvent.FriendlyName || 'Hardware Device') :
                                            selectedEvent.title || (selectedEvent as SystemLog).LevelDisplayName || 'System Event'}
                                </h2>
                                <p className="text-sm text-secondary">{selectedEvent.summary || selectedEvent.Message || selectedEvent.description || 'No description available.'}</p>
                            </div>

                            <InspectorSection title="Metadata">
                                <InspectorItem label="Time" value={selectedEvent.TimeCreated ? formatDateTime(parseDate(selectedEvent.TimeCreated)) : 'N/A'} />
                                {selectedEvent.Id && <InspectorItem label="Event ID" value={selectedEvent.Id} mono />}
                                <InspectorItem label="Provider" value={selectedEvent.type === 'whea' ? 'Microsoft-Windows-WHEA-Logger' : selectedEvent.type === 'hardware' ? 'PCIeFabric' : selectedEvent.type === 'signal' ? `SentinelSignal [${selectedEvent.class}]` : (selectedEvent.ProviderName || 'System')} />
                                <InspectorItem label="Level" value={selectedEvent.type === 'whea' ? 'Critical' : selectedEvent.type === 'hardware' ? 'Info' : selectedEvent.severity || ((selectedEvent as SystemLog).LevelDisplayName || 'Info')} />
                            </InspectorSection>

                            {selectedEvent.type === 'whea' && (selectedEvent as any).DecodedCPER && (
                                <InspectorSection title="Decoded CPER (WHEA)">
                                    <CollapsibleCode data={(selectedEvent as any).DecodedCPER} label="CPER Structure" />
                                </InspectorSection>
                            )}

                            {selectedEvent.type === 'system' && (selectedEvent as SystemLog).Properties && (
                                <InspectorSection title="Properties">
                                    <CollapsibleCode data={(selectedEvent as SystemLog).Properties} label="Event Properties" />
                                </InspectorSection>
                            )}

                            {/* Generic Reporting for Diagnostic/Power items */}
                            {(selectedEvent.type === 'diagnostic' || selectedEvent.type === 'state' || selectedEvent.type === 'policy' || selectedEvent.type === 'signal') && (selectedEvent.data || selectedEvent.evidence) && (
                                <InspectorSection title={selectedEvent.type === 'signal' ? "Signal Evidence" : "Deep Diagnostics Data"}>
                                    <CollapsibleCode data={selectedEvent.evidence || selectedEvent.data} label={selectedEvent.title || "Raw Telemetry"} />
                                </InspectorSection>
                            )}

                            {selectedEvent.type === 'hardware' && (selectedEvent.title === 'Core Topology' || selectedEvent.panelTitle === 'Core Topology') && (selectedEvent as any).data ? (
                                <InspectorSection title="Topology Details">
                                    <div className="grid grid-cols-2 gap-2 mb-4">
                                        <InspectorItem label="Physical Cores" value={(selectedEvent as any).data.cores || (selectedEvent as any).data.NumberOfCores || 'N/A'} />
                                        <InspectorItem label="Logical Threads" value={(selectedEvent as any).data.threads || (selectedEvent as any).data.NumberOfLogicalProcessors || 'N/A'} />
                                        <InspectorItem label="Sockets" value={(selectedEvent as any).data.sockets || 1} />
                                        <InspectorItem label="L2 Cache" value={(selectedEvent as any).data.l2_cache_kb ? `${Math.round((selectedEvent as any).data.l2_cache_kb / 1024)} MB` : 'N/A'} />
                                        <InspectorItem label="L3 Cache" value={(selectedEvent as any).data.l3_cache_kb ? `${Math.round((selectedEvent as any).data.l3_cache_kb / 1024)} MB` : 'N/A'} />
                                    </div>
                                    <CollapsibleCode data={selectedEvent} label="Raw Topology Data" />
                                </InspectorSection>
                            ) : selectedEvent.type === 'diagnostic' && (selectedEvent.title === 'Security Enclaves' || selectedEvent.title === 'Security & Virt') && (selectedEvent as any).data ? (
                                <InspectorSection title="Security State">
                                    <div className="space-y-4 mb-4">
                                        <div className="flex justify-between items-center bg-surface-2 p-2 rounded">
                                            <span className="text-secondary text-sm">VBS Enclave</span>
                                            {(selectedEvent as any).data.vbs?.Running ?
                                                <span className="text-green-400 font-bold text-xs flex items-center gap-1"><Shield className="w-3 h-3" /> SECURE</span> :
                                                <span className="text-secondary font-bold text-xs">DISABLED</span>
                                            }
                                        </div>
                                        <div className="flex justify-between items-center bg-surface-2 p-2 rounded">
                                            <span className="text-secondary text-sm">Hypervisor</span>
                                            {(selectedEvent as any).data.hyperv?.Present ?
                                                <span className="text-blue-400 font-bold text-xs flex items-center gap-1"><Activity className="w-3 h-3" /> ONLINE</span> :
                                                <span className="text-secondary font-bold text-xs">OFFLINE</span>
                                            }
                                        </div>
                                        <div className="flex justify-between items-center bg-surface-2 p-2 rounded">
                                            <span className="text-secondary text-sm">Virtualization Firmware</span>
                                            {(selectedEvent as any).data.firmware_enabled ?
                                                <span className="text-primary font-bold text-xs">ENABLED</span> :
                                                <span className="text-secondary font-bold text-xs">DISABLED</span>
                                            }
                                        </div>
                                    </div>
                                    <CollapsibleCode data={(selectedEvent as any).data} label="Raw Security Data" />
                                </InspectorSection>
                            ) : (selectedEvent.type === 'hardware' || selectedEvent.type === 'diagnostic') && (
                                <InspectorSection title="Device Properties">
                                    <CollapsibleCode data={selectedEvent} label="Raw Data" />
                                </InspectorSection>
                            )}
                        </Inspector>
                    )}

                    <ContextComposer />
                    <SmartContextControl availableItems={availableForContext} />
                </main>
            </div>
        </div >
    );
};

// Quick helper
const CopyLinkIcon: React.FC<{ className?: string }> = ({ className }) => (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></svg>
);
