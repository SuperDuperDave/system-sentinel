import { Activity, AlertTriangle, Cpu, HardDrive, Layers } from 'lucide-react';
import React from 'react';
import { WidgetActiveIncidents, WidgetWheaBurst } from './WidgetStatCards';
import { WidgetSystemSnapshot } from './WidgetSystemSnapshot';
import { WidgetDriverChanges } from './WidgetDriverChanges';
import { WidgetRecentCritical } from './WidgetRecentCritical';
import { WidgetSiliconHealth } from './WidgetSiliconHealth';

export interface WidgetDefinition {
    id: string;
    title: string;
    description: string;
    icon: React.ElementType;
    defaultEnabled: boolean;
    component: React.FC<any>;
}

export const WIDGET_REGISTRY: WidgetDefinition[] = [
    {
        id: 'silicon_health',
        title: 'Silicon Health',
        description: 'Deep diagnostic hardware status.',
        icon: Activity,
        defaultEnabled: true,
        component: WidgetSiliconHealth
    },
    {
        id: 'active_incidents',
        title: 'Active Incidents',
        description: 'Current system stability overview and tiered incidents.',
        icon: Activity,
        defaultEnabled: true,
        component: WidgetActiveIncidents
    },
    {
        id: 'recent_critical',
        title: 'Recent Critical Events',
        description: 'Latest Tier-1 hardware and system failures.',
        icon: AlertTriangle,
        defaultEnabled: true,
        component: WidgetRecentCritical
    },
    {
        id: 'whea_burst',
        title: 'WHEA Burst Detector',
        description: 'Real-time monitoring of WHEA error bursts.',
        icon: Cpu,
        defaultEnabled: true,
        component: WidgetWheaBurst
    },
    {
        id: 'system_snapshot',
        title: 'System Snapshot',
        description: 'Live CPU, RAM, and Uptime metrics.',
        icon: Layers,
        defaultEnabled: true,
        component: WidgetSystemSnapshot
    },
    {
        id: 'driver_changes',
        title: 'Recent Driver Changes',
        description: 'History of driver installations and updates.',
        icon: HardDrive,
        defaultEnabled: false,
        component: WidgetDriverChanges
    }
];
