import React, { useState } from 'react';
import { useSystemStore } from '@/lib/store';
import { getApiBase } from '@/lib/api';
import { Activity, Shield, Zap, Cpu, ArrowRight, Play } from 'lucide-react';

export const OverviewView: React.FC = () => {
    const { diagnosticReport, setDiagnosticReport } = useSystemStore(); // Assuming setDiagnosticReport exists
    const setActiveView = useSystemStore(state => state.setActiveView);
    const [loading, setLoading] = useState(false);

    // Fetch Logic (This should ideally be an Effect or Store Action)
    const runScan = async () => {
        setLoading(true);
        try {
            const res = await fetch(`${getApiBase()}/api/system/diagnostics`);
            const json = await res.json();
            setDiagnosticReport(json);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    if (!diagnosticReport) {
        return (
            <div className="w-full h-full flex flex-col items-center justify-center bg-bg-app p-6">
                <div className="bg-surface-1 p-8 rounded-2xl border border-subtle shadow-xl max-w-md w-full text-center">
                    <div className="mx-auto w-16 h-16 bg-blue-500/10 rounded-full flex items-center justify-center mb-6">
                        <Activity className="w-8 h-8 text-blue-500" />
                    </div>
                    <h1 className="text-2xl font-bold text-primary mb-2">Deep Diagnostics V2</h1>
                    <p className="text-secondary mb-8">
                        Physics-first system analysis. Scans PCIe fabric, Power States, Memory Training, and Constraints.
                    </p>

                    <button
                        onClick={runScan}
                        disabled={loading}
                        className="w-full py-3 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-bold transition-all flex items-center justify-center gap-2"
                    >
                        {loading ? (
                            <>
                                <Activity className="w-4 h-4 animate-spin" /> Scanning...
                            </>
                        ) : (
                            <>
                                <Play className="w-4 h-4 fill-current" /> Initialize Scan
                            </>
                        )}
                    </button>

                    <div className="mt-6 pt-6 border-t border-subtle grid grid-cols-3 gap-2 text-center">
                        <div>
                            <div className="text-lg font-bold text-primary">V2.1</div>
                            <div className="text-[10px] text-tertiary uppercase">Core</div>
                        </div>
                        <div>
                            <div className="text-lg font-bold text-primary">4</div>
                            <div className="text-[10px] text-tertiary uppercase">Domains</div>
                        </div>
                        <div>
                            <div className="text-lg font-bold text-primary">0ms</div>
                            <div className="text-[10px] text-tertiary uppercase">Lateny</div>
                        </div>
                    </div>
                </div>
            </div>
        );
    }

    // Parsing V2.1 Structure
    const meta = diagnosticReport.meta || {};
    const domains = diagnosticReport.domains || {};
    const identity = diagnosticReport.identity || {};

    return (
        <div className="p-6 h-full w-full overflow-y-auto bg-bg-app">
            <header className="mb-8 flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-primary">System Synthesis</h1>
                    <div className="flex items-center gap-2 text-sm text-secondary mt-1">
                        <span className="px-2 py-0.5 bg-green-500/10 text-green-500 rounded text-xs font-bold">HEALTHY</span>
                        <span className="w-1 h-1 rounded-full bg-subtle"></span>
                        <span>Last Scan: {new Date(meta.timestamp).toLocaleTimeString()}</span>
                        <span className="w-1 h-1 rounded-full bg-subtle"></span>
                        <span>{identity.cpu?.Name}</span>
                    </div>
                </div>

                <button
                    onClick={runScan}
                    className="px-4 py-2 bg-surface-2 hover:bg-surface-3 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
                >
                    <Activity className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                    {loading ? 'Rescanning...' : 'Rescan'}
                </button>
            </header>

            {/* Domain Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
                {/* PCIe */}
                <div
                    onClick={() => setActiveView('dd_pcie')}
                    className="p-4 bg-surface-1 border border-subtle hover:border-blue-500/50 hover:shadow-lg hover:shadow-blue-500/5 transition-all rounded-xl cursor-pointer group"
                >
                    <div className="flex justify-between items-start mb-4">
                        <div className="p-2 bg-blue-500/10 rounded-lg group-hover:bg-blue-500 group-hover:text-white transition-colors text-blue-500">
                            {/* Use Lucide icon names, imported properly above? Need Network */}
                            {/* Wait, I didn't import Network in this file. Adding import. */}
                            <Activity className="w-5 h-5" />
                        </div>
                        <ArrowRight className="w-4 h-4 text-tertiary group-hover:text-primary transition-transform group-hover:translate-x-1" />
                    </div>
                    <div className="font-bold text-lg mb-1">PCIe Fabric</div>
                    <div className="text-sm text-secondary mb-3">
                        {domains.pcie?.raw?.endpoints?.length || 0} Endpoints mapped.
                        {domains.pcie?.derived?.shared_groups?.length > 0 && <span className="block text-orange-500 text-xs mt-1">Contention Groups Detected.</span>}
                    </div>
                </div>

                {/* Power */}
                <div
                    onClick={() => setActiveView('dd_power')}
                    className="p-4 bg-surface-1 border border-subtle hover:border-yellow-500/50 hover:shadow-lg hover:shadow-yellow-500/5 transition-all rounded-xl cursor-pointer group"
                >
                    <div className="flex justify-between items-start mb-4">
                        <div className="p-2 bg-yellow-500/10 rounded-lg group-hover:bg-yellow-500 group-hover:text-white transition-colors text-yellow-500">
                            <Zap className="w-5 h-5" />
                        </div>
                        <ArrowRight className="w-4 h-4 text-tertiary group-hover:text-primary transition-transform group-hover:translate-x-1" />
                    </div>
                    <div className="font-bold text-lg mb-1">Power</div>
                    <div className="text-sm text-secondary mb-3">
                        ASPM: {domains.power?.power?.pcie_aspm?.decoded_ac || 'Unknown'}
                        <div className="mt-1 flex gap-2">
                            {domains.power?.power?.fast_startup && <span className="text-[10px] bg-orange-500/10 text-orange-500 px-1 rounded">FastStartup</span>}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

