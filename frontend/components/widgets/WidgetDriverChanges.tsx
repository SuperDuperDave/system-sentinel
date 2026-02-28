import React, { useEffect, useState } from 'react';
import { HardDrive } from 'lucide-react';

interface DriverChange {
    deviceName: string;
    driverVersion: string;
    driverProvider: string;
    driverDate: string;
}

import { useSystemStore } from '@/lib/store';
import { getApiBase } from '@/lib/api';

export const WidgetDriverChanges: React.FC = () => {
    const { setActiveView } = useSystemStore();
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

    if (loading) return <div className="h-32 animate-pulse bg-surface-1 rounded-lg"></div>;

    return (
        <div
            onClick={() => setActiveView('drivers')}
            className="bg-surface-1 border border-subtle rounded-lg p-4 flex flex-col gap-3 h-64 overflow-hidden cursor-pointer hover:border-primary transition-colors hover:shadow-lg"
        >
            <div className="flex items-center justify-between text-secondary text-sm font-medium uppercase tracking-wider">
                <div className="flex items-center gap-2">
                    <HardDrive className="w-4 h-4" />
                    <span>Recent Driver Updates</span>
                </div>
                <span className="text-xs bg-surface-2 px-2 py-0.5 rounded-full">{drivers.length}</span>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar -mr-2 pr-2">
                <div className="flex flex-col gap-2">
                    {drivers.length === 0 ? (
                        <div className="text-secondary text-sm italic py-2">No recent driver changes detected.</div>
                    ) : (
                        drivers.map((d, i) => (
                            <div key={i} className="flex flex-col text-sm border-b border-subtle pb-2 last:border-0 hover:bg-surface-2 p-1 rounded transition-colors">
                                <span className="font-medium text-primary truncate" title={d.deviceName}>{d.deviceName}</span>
                                <div className="flex justify-between text-xs text-secondary mt-1">
                                    <span>v{d.driverVersion}</span>
                                    <span>{d.driverDate}</span>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>
    );
};
