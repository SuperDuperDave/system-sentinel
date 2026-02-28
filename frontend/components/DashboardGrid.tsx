import React from 'react';
import { useDashboardStore } from '../lib/store';
import { WIDGET_REGISTRY } from './widgets/Registry';

export const DashboardGrid: React.FC = () => {
    const { widgets, order } = useDashboardStore();

    // Filter enabled widgets and sort by order
    const activeWidgets = order
        .filter(id => widgets[id])
        .map(id => WIDGET_REGISTRY.find(w => w.id === id))
        .filter((w): w is typeof WIDGET_REGISTRY[0] => !!w);

    return (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 p-6 pb-24">
            {activeWidgets.map((widget) => {
                const Component = widget.component;
                return (
                    <div key={widget.id} className="min-h-[250px] animate-in fade-in zoom-in duration-300">
                        <Component />
                    </div>
                );
            })}
        </div>
    );
};
