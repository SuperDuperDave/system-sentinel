import React, { useEffect, useState } from 'react';
import { HardDrive, Download, ChevronRight } from 'lucide-react';
import { useSystemStore } from '@/lib/store';
import { getApiBase } from '@/lib/api';

// Reusing types or define new ones. 
// Ideally should share types in lib/types.ts, but for now defining here or importing if available.
interface DriverChange {
    deviceName: string;
    driverVersion: string;
    driverProvider: string;
    driverDate: string;
    infName: string;
}

export const DriversView: React.FC = () => {
    const [drivers, setDrivers] = useState<DriverChange[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetch(`${getApiBase()}/api/system/driver-changes`)
            .then(res => res.json())
            .then(data => {
                setDrivers(Array.isArray(data) ? data : []);
                setLoading(false);
            })
            .catch(e => {
                console.error(e);
                setLoading(false);
            });
    }, []);

    return (
        <div className="flex flex-col h-full overflow-hidden p-6 gap-6">
            <header className="flex flex-col gap-2">
                <div className="flex items-center gap-3 text-primary">
                    <div className="p-2 bg-surface-2 rounded-lg">
                        <HardDrive className="w-6 h-6 text-emerald-500" />
                    </div>
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight">Driver Updates</h1>
                        <p className="text-secondary text-sm">Recent driver installations and updates detected on this system.</p>
                    </div>
                </div>
            </header>

            <div className="flex-1 bg-surface-1 border border-subtle rounded-lg overflow-hidden flex flex-col">
                <div className="grid grid-cols-12 gap-4 p-4 border-b border-subtle text-xs font-bold text-secondary uppercase tracking-wider bg-surface-2/50">
                    <div className="col-span-4">Device Name</div>
                    <div className="col-span-2">Version</div>
                    <div className="col-span-3">Provider</div>
                    <div className="col-span-2">Date</div>
                    <div className="col-span-1">INF</div>
                </div>

                <div className="overflow-y-auto custom-scrollbar flex-1">
                    {loading ? (
                        <div className="p-8 flex items-center justify-center text-secondary">Loading drivers...</div>
                    ) : drivers.length === 0 ? (
                        <div className="p-8 flex items-center justify-center text-secondary flex-col gap-2">
                            <HardDrive className="w-8 h-8 text-tertiary" />
                            <span>No recent driver changes found.</span>
                        </div>
                    ) : (
                        drivers.map((d, i) => (
                            <div key={i} className="grid grid-cols-12 gap-4 p-4 border-b border-subtle last:border-0 hover:bg-surface-2 transition-colors text-sm items-center">
                                <div className="col-span-4 font-medium text-primary flex items-center gap-2 truncate" title={d.deviceName}>
                                    <div className="w-1.5 h-1.5 rounded-full bg-emerald-500"></div>
                                    {d.deviceName}
                                </div>
                                <div className="col-span-2 font-mono text-secondary text-xs">{d.driverVersion}</div>
                                <div className="col-span-3 text-secondary">{d.driverProvider}</div>
                                <div className="col-span-2 text-secondary">{d.driverDate}</div>
                                <div className="col-span-1 text-xs text-tertiary truncate" title={d.infName}>{d.infName || '-'}</div>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>
    );
};
