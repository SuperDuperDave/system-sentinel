import React from 'react';
import { LayoutDashboard, Database, Activity, Settings, FileText, Zap, Cpu, Server, HardDrive, AlertTriangle, Home, Shield, Network, Grid, ShieldAlert, Monitor, CircuitBoard, Search } from 'lucide-react';
import { useSystemStore } from '@/lib/store';

export const Sidebar: React.FC = () => {
    // Explicitly select properties from store to avoid reactivity issues with whole store object
    const activeView = useSystemStore(state => state.activeView);
    const setActiveView = useSystemStore(state => state.setActiveView);

    const navItems = [
        { id: 'overview', icon: Home, label: 'Overview' },
        { id: 'events', icon: Activity, label: 'System Events' },
        { id: 'whea', icon: Shield, label: 'Hardware Errors' },
        // Deep Diagnostics split into sub-pages below
        { id: 'drivers', icon: HardDrive, label: 'Drivers' },
        { id: 'hardware', icon: Cpu, label: 'Hardware Specs' },
        { id: 'dumps', icon: Database, label: 'Crash Dumps' },
    ];

    return (
        <div className="w-16 lg:w-64 h-full border-r border-subtle bg-surface-1 flex flex-col justify-between transition-all duration-300">
            <div className="flex flex-col py-4 gap-1">
                <div className="px-6 mb-6 flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center shrink-0">
                        <Shield className="w-5 h-5 text-white" />
                    </div>
                    <span className="font-bold text-lg hidden lg:block tracking-tight">Sentinel</span>
                </div>

                <nav className="flex flex-col gap-6 px-3 mt-4">
                    <div className="flex flex-col gap-0.5">
                        <button
                            onClick={() => setActiveView('overview')}
                            className={`
                                flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer
                                ${activeView === 'overview'
                                    ? 'bg-surface-active text-primary'
                                    : 'text-secondary hover:bg-surface-2 hover:text-primary'}
                            `}
                        >
                            <Home className="w-4 h-4" />
                            <span className="hidden lg:block">Overview</span>
                        </button>
                    </div>

                    <div className="flex flex-col gap-1">
                        <span className="px-3 text-xs font-bold text-tertiary uppercase tracking-wider hidden lg:block">Monitoring</span>
                        {[
                            { id: 'events', icon: Activity, label: 'System Events' },
                            { id: 'whea_corrected', icon: Zap, label: 'Storm Radar' },
                            { id: 'whea', icon: Shield, label: 'Raw WHEA Events' },
                            { id: 'dumps', icon: Database, label: 'Crash Dumps' }
                        ].map((item) => (
                            <button
                                key={item.id}
                                onClick={() => setActiveView(item.id)}
                                className={`
                                    flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer
                                    ${activeView === item.id
                                        ? 'bg-surface-active text-primary'
                                        : 'text-secondary hover:bg-surface-2 hover:text-primary'}
                                `}
                            >
                                <item.icon className="w-4 h-4" />
                                <span className="hidden lg:block">{item.label}</span>
                            </button>
                        ))}
                    </div>

                    <div className="flex flex-col gap-1">
                        <span className="px-3 text-xs font-bold text-tertiary uppercase tracking-wider hidden lg:block">Deep Diagnostics</span>
                        {[
                            { id: 'dd_overview', icon: Activity, label: 'Overview' },
                            { id: 'dd_pcie', icon: Network, label: 'PCIe Fabric' },
                            { id: 'dd_power', icon: Zap, label: 'Power & Transitions' },
                            { id: 'dd_memory', icon: Grid, label: 'Memory & Stability' },
                            { id: 'forensic_signals', icon: Search, label: 'Forensic Signals' }
                        ].map((item) => (
                            <button
                                key={item.id}
                                onClick={() => setActiveView(item.id)}
                                className={`
                                    flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer
                                    ${activeView === item.id
                                        ? 'bg-surface-active text-primary'
                                        : 'text-secondary hover:bg-surface-2 hover:text-primary'}
                                `}
                            >
                                <item.icon className="w-4 h-4" />
                                <span className="hidden lg:block">{item.label}</span>
                            </button>
                        ))}
                    </div>

                    <div className="flex flex-col gap-1">
                        <span className="px-3 text-xs font-bold text-tertiary uppercase tracking-wider hidden lg:block">System</span>
                        {[
                            { id: 'hardware', icon: Cpu, label: 'Overview' },
                            { id: 'cpu', icon: Server, label: 'CPU & Platform' },
                            { id: 'gpu', icon: Monitor, label: 'GPU & Display' },
                            { id: 'board', icon: CircuitBoard, label: 'Motherboard & Firmware' },
                            { id: 'storage', icon: Database, label: 'Storage Forensics' },
                            { id: 'network', icon: Network, label: 'Network Connectivity' },
                            { id: 'drivers', icon: HardDrive, label: 'Drivers' }
                        ].map((item) => (
                            <button
                                key={item.id}
                                onClick={() => setActiveView(item.id)}
                                className={`
                                    flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer
                                    ${activeView === item.id
                                        ? 'bg-surface-active text-primary'
                                        : 'text-secondary hover:bg-surface-2 hover:text-primary'}
                                `}
                            >
                                <item.icon className="w-4 h-4" />
                                <span className="hidden lg:block">{item.label}</span>
                            </button>
                        ))}
                    </div>

                    <div className="flex flex-col gap-1">
                        <span className="px-3 text-xs font-bold text-tertiary uppercase tracking-wider hidden lg:block">Intelligence</span>
                        <button
                            onClick={() => setActiveView('gallery')}
                            className={`
                                flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer
                                ${activeView === 'gallery'
                                    ? 'bg-surface-active text-primary'
                                    : 'text-secondary hover:bg-surface-2 hover:text-primary'}
                            `}
                        >
                            <Zap className="w-4 h-4 text-purple-500" />
                            <span className="hidden lg:block">Prompt Gallery</span>
                        </button>
                    </div>
                </nav>
            </div>

            <div className="p-4 border-t border-subtle hidden lg:block">
                <div className="text-xs text-tertiary">v0.2.2 • Local</div>
            </div>
        </div>
    );
};
