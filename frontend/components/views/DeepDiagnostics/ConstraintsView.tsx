import React from 'react';
import { useSystemStore } from '@/lib/store';
import { ShieldAlert, Info, Sliders, AlertTriangle } from 'lucide-react';

export const ConstraintsView: React.FC = () => {
    const report = useSystemStore(state => state.diagnosticReport);
    // V2 Data Structure: domains.constraints.constraints = array of { friendly_name, instance_id, problem_code, reason }
    // OR user_actions.constraints depending on how orchestrator aggregated it. 
    // Let's assume domains.constraints is the raw source.
    const constraintsData = report?.domains?.constraints || report?.user_actions;
    const constraints = constraintsData?.constraints || [];

    if (!report) {
        return (
            <div className="p-12 flex flex-col items-center justify-center text-secondary">
                <ShieldAlert className="w-12 h-12 mb-4 opacity-20" />
                <h2 className="text-xl font-bold mb-2">No Constraints Context</h2>
                <p>Please run a Deep Scan from the Overview or Sidebar.</p>
            </div>
        );
    }

    return (
        <div className="h-full w-full flex flex-col overflow-hidden bg-bg-app">
            <div className="px-6 py-4 border-b border-subtle flex items-center justify-between bg-surface-1">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-blue-500/10 rounded-lg">
                        <ShieldAlert className="w-5 h-5 text-blue-500" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-primary">Constraints & Experiments</h1>
                        <p className="text-xs text-secondary">User Interventions • Disabled Hardware • Manual Overrides</p>
                    </div>
                </div>
                <div className="text-xs text-tertiary font-mono">
                    {constraints.length} Active Constraint{constraints.length !== 1 ? 's' : ''}
                </div>
            </div>

            <div className="p-6 overflow-y-auto custom-scrollbar space-y-6">

                {/* Introduction Card */}
                <div className="bg-surface-1 border border-subtle rounded-xl p-4 flex gap-4">
                    <div className="p-2 bg-surface-2 rounded-lg h-fit">
                        <Info className="w-5 h-5 text-blue-500" />
                    </div>
                    <div>
                        <h3 className="text-sm font-bold text-primary mb-1">What are Constraints?</h3>
                        <p className="text-xs text-secondary leading-relaxed max-w-2xl">
                            When you manually disable a device in Device Manager to stop a crash, you create a <strong>Constraint</strong>.
                            This is a high-ranking signal for diagnostics: it represents <em>User Intent</em> as a workaround for hardware instability.
                            System Sentinel tracks these interventions as primary evidence.
                        </p>
                    </div>
                </div>

                {/* Constraints List */}
                <div>
                    <h3 className="text-sm font-bold text-secondary uppercase tracking-wider mb-3">Active Interventions</h3>

                    {constraints.length === 0 ? (
                        <div className="p-12 text-center border border-dashed border-subtle rounded-xl text-secondary">
                            <div className="flex justify-center mb-2">
                                <CheckCircleIcon className="w-8 h-8 text-green-500/30" />
                            </div>
                            <p className="font-medium text-primary">No constraints detected.</p>
                            <p className="text-xs mt-1">All hardware devices are enabled and responding.</p>
                        </div>
                    ) : (
                        <div className="space-y-3">
                            {constraints.map((c: any, i: number) => (
                                <div key={i} className="bg-surface-1 border border-red-500/30 hover:border-red-500/50 transition-colors rounded-xl p-4 shadow-sm group">
                                    <div className="flex items-start justify-between mb-2">
                                        <div className="flex items-center gap-2">
                                            <div className="p-1.5 bg-red-500/10 rounded">
                                                <Sliders className="w-4 h-4 text-red-500" />
                                            </div>
                                            <span className="font-bold text-primary">{c.friendly_name}</span>
                                        </div>
                                        <span className="bg-red-500 text-white text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wide">
                                            Disabled (Code 22)
                                        </span>
                                    </div>

                                    <div className="pl-9">
                                        <div className="text-xs font-mono text-tertiary mb-2">{c.instance_id}</div>

                                        <div className="flex items-start gap-2 p-3 bg-red-500/5 rounded border border-red-500/10">
                                            <AlertTriangle className="w-3.5 h-3.5 text-red-500 mt-0.5 shrink-0" />
                                            <div className="text-xs text-secondary">
                                                <strong>Inference:</strong> Because this device is manually disabled, it is likely a source of instability or resource conflict.
                                                Diagnostics will treat this device as "Known Bad" or "Suspect" in analysis chains.
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

const CheckCircleIcon = ({ className }: { className?: string }) => (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><path d="m9 11 3 3L22 4" /></svg>
);
