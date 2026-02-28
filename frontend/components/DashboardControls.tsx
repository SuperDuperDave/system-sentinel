import React, { useState } from 'react';
import { Settings, X, Wifi, WifiOff, Save, RefreshCw } from 'lucide-react';
import { useDashboardStore, useSystemStore } from '../lib/store';
import { fetchWheaSettings, updateWheaSettings, WheaSettings } from '../lib/api';
import { WIDGET_REGISTRY } from './widgets/Registry';

export const DashboardControls: React.FC = () => {
    const [isOpen, setIsOpen] = useState(false);
    const { widgets, toggleWidget } = useDashboardStore();
    const { liveFeedEnabled, toggleLiveFeed, streamStatus } = useSystemStore();

    // WHEA Settings State
    const [wheaConf, setWheaConf] = useState<WheaSettings | null>(null);
    const [wheaLoading, setWheaLoading] = useState(false);

    React.useEffect(() => {
        if (isOpen) {
            setWheaLoading(true);
            fetchWheaSettings().then(setWheaConf).catch(console.error).finally(() => setWheaLoading(false));
        }
    }, [isOpen]);

    const handleSaveWhea = async () => {
        if (!wheaConf) return;
        setWheaLoading(true);
        try {
            await updateWheaSettings(wheaConf);
        } catch (e) {
            console.error(e);
        } finally {
            setWheaLoading(false);
        }
    };

    return (
        <>
            {/* FAB */}
            <button
                onClick={() => setIsOpen(true)}
                className="fixed top-6 right-6 p-3 bg-primary text-bg-app rounded-full shadow-lg hover:bg-white transition-colors z-[60] transform hover:scale-105 active:scale-95"
                title="Dashboard Settings"
            >
                <Settings className="w-6 h-6" />
            </button>

            {/* Overlay */}
            {isOpen && (
                <div
                    className="fixed inset-0 bg-black/50 z-50 backdrop-blur-sm"
                    onClick={() => setIsOpen(false)}
                />
            )}

            {/* Panel */}
            <div className={`fixed inset-y-0 right-0 w-80 bg-surface-1 border-l border-subtle shadow-2xl z-50 transform transition-transform duration-300 ease-in-out ${isOpen ? 'translate-x-0' : 'translate-x-full'}`}>
                <div className="flex flex-col h-full">
                    <div className="flex items-center justify-between p-4 border-b border-subtle bg-surface-2/50 backdrop-blur">
                        <span className="font-medium text-lg">Configuration</span>
                        <button onClick={() => setIsOpen(false)} className="text-secondary hover:text-primary p-1 rounded hover:bg-surface-2 transition-colors">
                            <X className="w-5 h-5" />
                        </button>
                    </div>

                    <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-6 custom-scrollbar">
                        {/* Live Feed Toggle */}
                        <div className="flex flex-col gap-3 p-4 bg-surface-2 rounded-lg border border-subtle">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    {liveFeedEnabled ? <Wifi className="w-5 h-5 text-green-500" /> : <WifiOff className="w-5 h-5 text-secondary" />}
                                    <div className="flex flex-col">
                                        <span className="font-medium text-sm">Live Feed</span>
                                        <span className="text-xs text-secondary">Real-time SSE Event Stream</span>
                                    </div>
                                </div>
                                <button
                                    onClick={toggleLiveFeed}
                                    className={`w-11 h-6 rounded-full relative transition-colors ${liveFeedEnabled ? 'bg-green-500' : 'bg-subtle'}`}
                                >
                                    <div className={`absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform shadow-sm ${liveFeedEnabled ? 'translate-x-5' : 'translate-x-0'}`} />
                                </button>
                            </div>
                            <div className="flex items-center justify-between text-xs border-t border-subtle pt-2 mt-1">
                                <span className="text-secondary">Connection Status</span>
                                <span className={`uppercase font-mono ${streamStatus === 'connected' ? 'text-green-500' : streamStatus === 'reconnecting' ? 'text-yellow-500' : 'text-secondary'}`}>
                                    {streamStatus}
                                </span>
                            </div>
                        </div>

                        {/* API Base URL Configuration */}
                        <div className="flex flex-col gap-3 p-4 bg-surface-2 rounded-lg border border-subtle">
                            <div className="flex flex-col gap-1">
                                <span className="font-medium text-sm">Backend API URL</span>
                                <span className="text-xs text-secondary">Base URL for backend server</span>
                            </div>
                            <input
                                type="text"
                                value={useSystemStore(state => state.apiBaseUrl)}
                                onChange={(e) => useSystemStore.getState().setApiBaseUrl(e.target.value)}
                                className="px-3 py-2 text-sm font-mono bg-surface-1 border border-subtle rounded focus:border-primary focus:ring-1 focus:ring-primary outline-none transition-colors"
                                placeholder="http://localhost:8001"
                            />
                            <span className="text-[10px] text-tertiary">Changes apply immediately. Default: http://localhost:8001</span>
                        </div>

                        {/* WHEA Configuration */}
                        {wheaConf && (
                            <div className="flex flex-col gap-3 p-4 bg-surface-2 rounded-lg border border-subtle">
                                <div className="flex items-center justify-between">
                                    <div className="flex flex-col gap-1">
                                        <span className="font-medium text-sm">Corrected WHEA Radar</span>
                                        <span className="text-xs text-secondary">Tunable Storm Detection</span>
                                    </div>
                                    <button
                                        onClick={handleSaveWhea}
                                        disabled={wheaLoading}
                                        className="p-1.5 bg-primary text-bg-app rounded hover:bg-white transition-colors disabled:opacity-50"
                                        title="Save Configuration"
                                    >
                                        {wheaLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                                    </button>
                                </div>

                                <div className="grid grid-cols-2 gap-3">
                                    <div className="flex flex-col gap-1">
                                        <label className="text-[10px] text-tertiary uppercase font-bold">History (Days)</label>
                                        <input
                                            type="number"
                                            value={wheaConf.history_days}
                                            onChange={e => setWheaConf({ ...wheaConf, history_days: parseInt(e.target.value) || 30 })}
                                            className="px-2 py-1 text-xs bg-surface-1 border border-subtle rounded text-primary"
                                        />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                        <label className="text-[10px] text-tertiary uppercase font-bold">Bucket (Sec)</label>
                                        <input
                                            type="number"
                                            value={wheaConf.bucket_seconds}
                                            onChange={e => setWheaConf({ ...wheaConf, bucket_seconds: parseInt(e.target.value) || 60 })}
                                            className="px-2 py-1 text-xs bg-surface-1 border border-subtle rounded text-primary"
                                        />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                        <label className="text-[10px] text-tertiary uppercase font-bold">Burst Thresh</label>
                                        <input
                                            type="number"
                                            value={wheaConf.burst_threshold}
                                            onChange={e => setWheaConf({ ...wheaConf, burst_threshold: parseInt(e.target.value) || 5 })}
                                            className="px-2 py-1 text-xs bg-surface-1 border border-subtle rounded text-primary"
                                        />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                        <label className="text-[10px] text-tertiary uppercase font-bold">Accel Thresh</label>
                                        <input
                                            type="number" step="0.1"
                                            value={wheaConf.accel_threshold}
                                            onChange={e => setWheaConf({ ...wheaConf, accel_threshold: parseFloat(e.target.value) || 2.0 })}
                                            className="px-2 py-1 text-xs bg-surface-1 border border-subtle rounded text-primary"
                                        />
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Widgets List */}
                        <div className="flex flex-col gap-3">
                            <span className="text-xs font-bold text-secondary uppercase tracking-wider px-1">Widgets</span>
                            {WIDGET_REGISTRY.map(w => (
                                <div key={w.id} className="flex items-center justify-between p-2 hover:bg-surface-2 rounded-lg transition-colors group">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 bg-surface-2 group-hover:bg-bg-app rounded-md transition-colors">
                                            <w.icon className="w-4 h-4 text-secondary group-hover:text-primary" />
                                        </div>
                                        <div className="flex flex-col">
                                            <span className="text-sm font-medium">{w.title}</span>
                                            <span className="text-xs text-secondary line-clamp-1">{w.description}</span>
                                        </div>
                                    </div>
                                    <input
                                        type="checkbox"
                                        checked={widgets[w.id] ?? w.defaultEnabled}
                                        onChange={() => toggleWidget(w.id)}
                                        className="accent-primary w-4 h-4 cursor-pointer"
                                    />
                                </div>
                            ))}
                        </div>
                    </div>

                    <div className="p-4 border-t border-subtle text-center">
                        <span className="text-xs text-tertiary">System Sentinel v0.2.0</span>
                    </div>
                </div>
            </div>
        </>
    );
};
