import React, { useEffect, useState } from 'react';
import { Activity, CheckCircle, AlertTriangle, ArrowRight } from 'lucide-react';
import { useSystemStore } from '@/lib/store';

export const WidgetSiliconHealth: React.FC = () => {
    const { setActiveView } = useSystemStore();
    // Simple mock logic for now, eventually query cache or store
    const [status, setStatus] = useState<'ok' | 'warning' | 'critical'>('ok');

    return (
        <div className="bg-surface-1 border border-subtle rounded-xl p-5 flex flex-col justify-between h-full group hover:border-blue-500/50 transition-colors">
            <div className="flex justify-between items-start">
                <div>
                    <h3 className="text-sm font-bold text-secondary uppercase tracking-wider mb-1">Silicon Health</h3>
                    <div className="flex items-center gap-2">
                        {status === 'ok' && <CheckCircle className="w-5 h-5 text-green-500" />}
                        {status === 'warning' && <AlertTriangle className="w-5 h-5 text-orange-500" />}
                        {status === 'critical' && <AlertTriangle className="w-5 h-5 text-red-500" />}

                        <span className={`text-xl font-bold ${status === 'ok' ? 'text-green-500' :
                                status === 'warning' ? 'text-orange-500' : 'text-red-500'
                            }`}>
                            {status === 'ok' ? 'Nominal' : status === 'warning' ? 'Unstable' : 'Critical'}
                        </span>
                    </div>
                </div>
                <div className="p-2 bg-surface-2 rounded-lg group-hover:bg-blue-500/10 group-hover:text-blue-500 transition-colors">
                    <Activity className="w-5 h-5" />
                </div>
            </div>

            <div className="mt-4 text-sm text-secondary">
                <p>PCIe Fabric: <span className="text-primary font-medium">OK</span></p>
                <p>Memory Integrity: <span className="text-primary font-medium">JEDEC</span></p>
            </div>

            <button
                onClick={() => setActiveView('diagnostics')}
                className="mt-4 flex items-center gap-2 text-xs font-bold text-blue-500 hover:text-blue-400 transition-colors"
            >
                Run Deep Diagnostics <ArrowRight className="w-3 h-3" />
            </button>
        </div>
    );
};
