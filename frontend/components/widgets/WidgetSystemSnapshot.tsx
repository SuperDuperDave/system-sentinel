import React, { useEffect, useState } from 'react';
import { Activity, Clock, Cpu, HardDrive } from 'lucide-react';
import { useSystemStore } from '@/lib/store';
import { getApiBase } from '@/lib/api';

interface SystemSnapshot {
    CPU: number;
    TotalMemoryGB: number;
    FreeMemoryGB: number;
    Uptime: string;
    LastBoot: string;
}

export const WidgetSystemSnapshot: React.FC = () => {
    const { setActiveView } = useSystemStore();
    const [data, setData] = useState<SystemSnapshot | null>(null);
    const [loading, setLoading] = useState(true);

    const fetchData = async () => {
        try {
            const res = await fetch(`${getApiBase()}/api/system/snapshot`);
            const json = await res.json();
            setData(json);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
        const interval = setInterval(fetchData, 2000); // 2s for "live" feel
        return () => clearInterval(interval);
    }, []);

    if (loading || !data) return <div className="h-32 animate-pulse bg-surface-1 rounded-lg"></div>;

    const totalMem = data.TotalMemoryGB || 16;
    const freeMem = data.FreeMemoryGB || 0;
    const usedMem = totalMem - freeMem;
    const memPercent = (usedMem / totalMem) * 100;

    return (
        <div
            onClick={() => setActiveView('hardware')}
            className="bg-surface-1 border border-subtle rounded-lg p-4 flex flex-col gap-4 cursor-pointer hover:border-primary transition-colors hover:shadow-lg h-64"
        >
            <div className="flex items-center gap-2 text-secondary text-sm font-medium uppercase tracking-wider">
                <Activity className="w-4 h-4" />
                <span>System Status</span>
                {/* Live Indicator - Only show if we recently fetched data */}
                <span className="relative flex h-2 w-2 ml-auto" title="Live System Metrics">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                </span>
            </div>

            <div className="grid grid-cols-2 gap-4 flex-1">
                <div className="bg-surface-2 p-3 rounded-md flex flex-col gap-1 justify-center">
                    <span className="text-secondary text-xs">CPU Load</span>
                    <div className="flex items-baseline gap-1">
                        <span className="text-xl font-bold text-primary">{(data.CPU ?? 0).toFixed(1)}%</span>
                    </div>
                    {/* Tiny bar chart placeholder */}
                    <div className="h-1 bg-subtle w-full rounded-full mt-1">
                        <div className="h-full bg-blue-500 rounded-full" style={{ width: `${data.CPU}%` }}></div>
                    </div>
                </div>

                <div className="bg-surface-2 p-3 rounded-md flex flex-col gap-1 justify-center">
                    <span className="text-secondary text-xs">Memory</span>
                    <div className="flex items-baseline gap-1">
                        <span className="text-xl font-bold text-primary">{memPercent.toFixed(0)}%</span>
                        <span className="text-xs text-secondary">{usedMem.toFixed(1)} / {data.TotalMemoryGB} GB</span>
                    </div>
                    <div className="h-1 bg-subtle w-full rounded-full mt-1">
                        <div className="h-full bg-purple-500 rounded-full" style={{ width: `${memPercent}%` }}></div>
                    </div>
                </div>

                <div className="col-span-2 bg-surface-2 p-3 rounded-md flex items-center justify-between">
                    <div className="flex flex-col">
                        <span className="text-secondary text-xs">Uptime</span>
                        <span className="text-sm font-mono text-primary">{data.Uptime}</span>
                    </div>
                    <Clock className="w-4 h-4 text-tertiary" />
                </div>
            </div>
        </div>
    );
};
