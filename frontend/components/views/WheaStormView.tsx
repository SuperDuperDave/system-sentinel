import React, { useEffect, useState } from 'react';
import {
    Activity, AlertTriangle, CheckCircle, Shield,
    Zap, List, RefreshCw, ChevronDown, ChevronRight,
    Cpu, Clock, AlertOctagon, Hash
} from 'lucide-react';
import {
    fetchWheaBuckets, fetchWheaSignatures, fetchWheaStorm, triggerWheaIngest, generateWheaContext,
    WheaStorm, WheaSignature, WheaBucketsResponse
} from '@/lib/api';
import { useSystemStore } from '@/lib/store';
import { useContextStore } from '../context/ContextStore';
import { Inspector, InspectorSection, InspectorItem, CollapsibleCode } from '../Inspector';

// --- Components ---

const StatusCard: React.FC<{ storm: WheaStorm | null, loading: boolean }> = ({ storm, loading }) => {
    if (loading || !storm) {
        return <div className="h-32 bg-surface-1 rounded-lg animate-pulse border border-subtle"></div>;
    }

    const isCritical = storm.severity === 'critical';
    const isWarning = storm.severity === 'warning';
    const isActive = storm.status === 'active';

    // Color mapping
    const statusColor = isActive
        ? (isCritical ? 'text-red-400 border-red-500/30 bg-red-500/10' : 'text-amber-400 border-amber-500/30 bg-amber-500/10')
        : 'text-green-400 border-green-500/30 bg-green-500/10';

    const Icon = isActive ? (isCritical ? Zap : AlertTriangle) : CheckCircle;

    return (
        <div className={`p-4 rounded-lg border flex flex-col justify-between h-full bg-surface-1 min-h-[140px] ${statusColor.split(' ')[2] || 'bg-surface-1'} ${statusColor.split(' ')[1]}`}>
            <div className="flex items-start justify-between">
                <div>
                    <h3 className="text-sm font-bold uppercase tracking-wider opacity-80 flex items-center gap-2">
                        <Icon className="w-4 h-4" />
                        Storm Status
                    </h3>
                    <div className={`text-2xl font-bold mt-1 ${statusColor.split(' ')[0]}`}>
                        {isActive ? (isCritical ? 'CRITICAL STORM' : 'ELEVATED ACTIVITY') : 'NORMAL'}
                    </div>
                </div>
                {isActive && (
                    <div className="text-right">
                        <div className="text-xs uppercase opacity-70">Peak Rate</div>
                        <div className="text-xl font-mono font-bold">{storm.peak_rate.toFixed(1)} <span className="text-xs">/min</span></div>
                    </div>
                )}
            </div>

            <div className="mt-4 text-sm opacity-90 border-t border-black/10 pt-3 flex flex-col gap-1">
                <div className="font-medium">{storm.reason}</div>
            </div>
        </div>
    );
};

const TimelineChart: React.FC<{ data: WheaBucketsResponse | null }> = ({ data }) => {
    if (!data || !data.data || data.data.length === 0) {
        return (
            <div className="h-40 bg-surface-1 rounded-lg border border-subtle flex items-center justify-center text-secondary text-sm">
                No activity recorded in window.
            </div>
        );
    }

    const buckets = data.data;
    const maxVal = Math.max(...buckets.map(b => b.total), 5); // Min scale 5

    return (
        <div className="h-48 bg-surface-1 rounded-lg border border-subtle p-4 flex flex-col">
            <h3 className="text-xs font-bold text-secondary uppercase mb-2 flex items-center gap-2">
                <Activity className="w-3 h-3" /> Error Frequency (Last 24h)
            </h3>
            <div className="flex-1 flex items-end gap-[1px] w-full overflow-hidden">
                {buckets.map((b, idx) => {
                    const height = (b.total / maxVal) * 100;
                    // Color based on height logic
                    const colorClass = b.total > 20 ? 'bg-red-500' : b.total > 5 ? 'bg-amber-500' : 'bg-blue-500/50';

                    return (
                        <div
                            key={b.timestamp}
                            className={`flex-1 min-w-[2px] rounded-t-sm transition-all hover:bg-white ${colorClass}`}
                            style={{ height: `${Math.max(height, 5)}%` }}
                            title={`${new Date(b.timestamp * 1000).toLocaleTimeString()}: ${b.total} errors`}
                        />
                    );
                })}
            </div>
            {/* X Axis Labels (Simplified) */}
            <div className="flex justify-between text-[10px] text-tertiary mt-2">
                <span>{new Date(data.start_epoch * 1000).toLocaleTimeString()}</span>
                <span>{new Date(data.end_epoch * 1000).toLocaleTimeString()}</span>
            </div>
        </div>
    );
};

const SignatureCard: React.FC<{ sig: WheaSignature, rank: number, onClick: () => void, isSelected: boolean }> = ({ sig, rank, onClick, isSelected }) => {
    return (
        <div
            onClick={onClick}
            className={`
                group relative bg-surface-1 border rounded-lg p-4 cursor-pointer transition-all hover:bg-surface-2
                ${isSelected ? 'border-primary ring-1 ring-primary/50 bg-surface-2' : 'border-subtle hover:border-text-primary/30'}
            `}
        >
            {/* Rank Badge */}
            <div className="absolute top-3 right-3 text-[10px] font-bold text-tertiary opacity-50">
                #{rank}
            </div>

            <div className="flex flex-col h-full gap-3">
                {/* Header */}
                <div className="flex items-start gap-3">
                    <div className="p-2 rounded bg-surface-3 group-hover:bg-surface-active transition-colors">
                        <Cpu className="w-4 h-4 text-purple-400" />
                    </div>
                    <div>
                        <div className="text-xs font-bold uppercase text-secondary mb-0.5">{sig.error_type}</div>
                        <div className="font-medium text-sm text-primary line-clamp-2" title={sig.description}>
                            {sig.description}
                        </div>
                    </div>
                </div>

                <div className="h-px bg-subtle w-full" />

                {/* Metrics */}
                <div className="grid grid-cols-2 gap-2 mt-auto">
                    <div className="bg-surface-3/50 p-2 rounded">
                        <div className="text-lg font-mono font-bold text-primary leading-none">{sig.count_24h}</div>
                        <div className="text-[10px] text-tertiary uppercase mt-1">24h Count</div>
                    </div>
                    <div className="bg-surface-3/50 p-2 rounded">
                        <div className="text-lg font-mono font-bold text-secondary leading-none">{sig.count_total}</div>
                        <div className="text-[10px] text-tertiary uppercase mt-1">Total History</div>
                    </div>
                </div>

                <div className="flex items-center gap-1 text-[10px] text-tertiary">
                    <Clock className="w-3 h-3" />
                    <span>Last: {new Date(sig.last_seen).toLocaleString()}</span>
                </div>
            </div>
        </div>
    );
};

export const WheaStormView: React.FC = () => {
    const [storm, setStorm] = useState<WheaStorm | null>(null);
    const [signatures, setSignatures] = useState<WheaSignature[]>([]);
    const [buckets, setBuckets] = useState<WheaBucketsResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [selectedSig, setSelectedSig] = useState<WheaSignature | null>(null);

    const refresh = async () => {
        setLoading(true);
        try {
            await triggerWheaIngest(); // Trigger fetch first

            // Parallel load
            const [s, sigs, b] = await Promise.all([
                fetchWheaStorm(),
                fetchWheaSignatures('impact', 50),
                fetchWheaBuckets() // defaults to 24h
            ]);

            setStorm(s);
            setSignatures(sigs);
            setBuckets(b);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const handleAddToContext = async () => {
        try {
            const { context } = await generateWheaContext(false); // Summary only
            // Add to context store
            useContextStore.getState().addItem({
                title: "Corrected WHEA Storm Summary",
                description: `Storm Status: ${storm?.status} | Peak: ${storm?.peak_rate.toFixed(1)}/min`,
                type: 'text',
                data: context,
                provenance: {
                    source: 'Corrected WHEA Radar',
                    timestamp: new Date().toISOString()
                }
            });
        } catch (e) {
            console.error("Failed to generate context", e);
        }
    };

    useEffect(() => {
        refresh();
    }, []);

    return (
        <div className="flex h-full w-full overflow-hidden">
            {/* Main Content Area */}
            <div className="flex-1 overflow-y-auto custom-scrollbar bg-bg-app p-6">
                <header className="flex items-center justify-between mb-6">
                    <div>
                        <h1 className="text-2xl font-bold flex items-center gap-3">
                            <Shield className="w-6 h-6 text-purple-400" />
                            Corrected WHEA Storm Radar
                        </h1>
                        <p className="text-secondary text-sm mt-1">
                            Predictive hardware failure analysis based on corrected error acceleration.
                        </p>
                    </div>

                    <div className="flex items-center gap-2">
                        <button
                            onClick={handleAddToContext}
                            disabled={!storm}
                            className="flex items-center gap-2 px-3 py-2 bg-surface-1 hover:bg-surface-2 border border-subtle rounded transition-colors text-sm font-medium disabled:opacity-50"
                            title="Add Storm Summary to AI Context"
                        >
                            <List className="w-4 h-4" />
                            Add to Context
                        </button>
                        <button
                            onClick={refresh}
                            className="flex items-center gap-2 px-3 py-2 bg-surface-1 hover:bg-surface-2 border border-subtle rounded transition-colors text-sm font-medium"
                        >
                            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                            Refresh Analysis
                        </button>
                    </div>
                </header>

                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
                    {/* 1. Radar Status */}
                    <div className="lg:col-span-1">
                        <StatusCard storm={storm} loading={loading} />
                    </div>

                    {/* 2. Timeline Chart (24h) */}
                    <div className="lg:col-span-2">
                        <TimelineChart data={buckets} />
                    </div>
                </div>

                {/* 3. Signatures Grid */}
                <div className="space-y-4">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-bold flex items-center gap-2">
                            <Hash className="w-5 h-5 text-secondary" />
                            Top Failure Signatures
                        </h2>
                        <span className="text-xs text-tertiary">Ranked by 24h Impact</span>
                    </div>

                    {!loading && signatures.length === 0 && (
                        <div className="bg-surface-1 p-8 rounded-lg border border-subtle text-center text-secondary">
                            No error signatures detected in current history window. System appears healthy.
                        </div>
                    )}

                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {signatures.map((sig, idx) => (
                            <SignatureCard
                                key={sig.id}
                                sig={sig}
                                rank={idx + 1}
                                isSelected={selectedSig?.id === sig.id}
                                onClick={() => setSelectedSig(sig)}
                            />
                        ))}
                    </div>
                </div>
            </div>

            {/* Side Inspector Panel */}
            {selectedSig && (
                <Inspector
                    title="Signature Details"
                    onClose={() => setSelectedSig(null)}
                    onCopy={() => navigator.clipboard.writeText(JSON.stringify(selectedSig, null, 2))}
                    eventData={{ ...selectedSig, type: 'whea_signature' }} // Augment for ContextStore type logic if needed
                >
                    <div className="mb-6">
                        <div className="flex items-start gap-4 mb-4">
                            <div className="p-3 bg-surface-2 rounded-lg">
                                <AlertOctagon className="w-8 h-8 text-critical" />
                            </div>
                            <div>
                                <h2 className="text-lg font-bold text-primary mb-1">
                                    {selectedSig.error_type}
                                </h2>
                                <p className="text-sm text-secondary line-clamp-2">
                                    {selectedSig.description}
                                </p>
                            </div>
                        </div>
                    </div>

                    <InspectorSection title="Metadata">
                        <InspectorItem label="Last Seen" value={new Date(selectedSig.last_seen).toLocaleString()} />
                        <InspectorItem label="Signature ID" value={selectedSig.id} mono />
                        <InspectorItem label="Impact Score" value={selectedSig.count_24h} />
                        <InspectorItem label="Total Count" value={selectedSig.count_total} />
                    </InspectorSection>

                    {selectedSig.samples && selectedSig.samples.length > 0 && (
                        <InspectorSection title="Latest Error Message">
                            <div className="bg-surface-2 p-3 rounded border border-subtle text-xs font-mono text-secondary whitespace-pre-wrap break-words select-text">
                                {selectedSig.samples[selectedSig.samples.length - 1].msg}
                            </div>
                        </InspectorSection>
                    )}

                    <InspectorSection title="Raw Signature Data">
                        <CollapsibleCode data={selectedSig} label="Complete JSON" />
                    </InspectorSection>
                </Inspector>
            )}
        </div>
    );
};
