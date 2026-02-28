import { SystemLog, WheaLog } from './types';
import { useSystemStore } from './store';

// Helper to get current API base URL from store
export const getApiBase = () => useSystemStore.getState().apiBaseUrl;

export async function fetchSystemLogs(count: number = 10): Promise<SystemLog[]> {
    try {
        const res = await fetch(`${getApiBase()}/api/logs/system?count=${count}`);
        if (!res.ok) throw new Error('Failed to fetch system logs');
        return await res.json();
    } catch (error) {
        console.error("Error fetching system logs:", error);
        return [];
    }
}

export async function fetchWheaLogs(count: number = 20): Promise<WheaLog[]> {
    try {
        const res = await fetch(`${getApiBase()}/api/logs/whea?count=${count}`);
        if (!res.ok) throw new Error('Failed to fetch WHEA logs');
        return await res.json();
    } catch (error) {
        console.error("Error fetching WHEA logs:", error);
        return [];
    }
}

export async function createCapturePack(): Promise<boolean> {
    try {
        const res = await fetch(`${getApiBase()}/api/capture/create`, { method: 'POST' });
        if (!res.ok) throw new Error('Failed to create capture pack');

        // Handle Blob download
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        // Try to get filename from header or default
        const contentDisposition = res.headers.get('Content-Disposition');
        let filename = `SystemSentinel_Capture_${new Date().toISOString().slice(0, 10)}.zip`;
        if (contentDisposition) {
            const match = contentDisposition.match(/filename="?(.+)"?/);
            if (match) filename = match[1];
        }

        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        return true;
    } catch (error) {
        console.error("Error creating capture pack:", error);
        return false;
    }
}

export async function fetchPreCrashContext(timestamp: string, count: number = 50): Promise<SystemLog[]> {
    try {
        const res = await fetch(`${getApiBase()}/api/logs/context`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ timestamp, count })
        });
        if (!res.ok) throw new Error('Failed to fetch context logs');
        return await res.json();
    } catch (error) {
        console.error("Error fetching pre-crash context:", error);
        return [];
    }
}
// --- Corrected WHEA Types ---
export interface WheaSettings {
    history_days: number;
    bucket_seconds: number;
    burst_threshold: number;
    accel_threshold: number;
}

export interface WheaSignature {
    id: string;
    description: string;
    error_type: string;
    count_24h: number;
    count_total: number;
    last_seen: string;
    acceleration: number;
    samples: any[];
}

export interface WheaBucket {
    timestamp: number;
    total: number;
}

export interface WheaBucketsResponse {
    start_epoch: number;
    end_epoch: number;
    granularity: number;
    data: WheaBucket[];
}

export interface WheaStorm {
    id: string;
    start: string;
    end: string | null;
    status: 'active' | 'resolved';
    severity: 'warning' | 'critical';
    reason: string;
    peak_rate: number;
    dominant_signatures: string[];
}

// --- Corrected WHEA API ---

export async function fetchWheaSettings(): Promise<WheaSettings> {
    const res = await fetch(`${getApiBase()}/api/whea/settings`);
    if (!res.ok) throw new Error('Failed to fetch WHEA settings');
    return await res.json();
}

export async function updateWheaSettings(settings: WheaSettings): Promise<any> {
    const res = await fetch(`${getApiBase()}/api/whea/settings`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
    });
    if (!res.ok) throw new Error('Failed to update WHEA settings');
    return await res.json();
}

export async function fetchWheaBuckets(start?: number, end?: number): Promise<WheaBucketsResponse> {
    const params = new URLSearchParams();
    if (start) params.append('start', start.toString());
    if (end) params.append('end', end.toString());

    const res = await fetch(`${getApiBase()}/api/whea/buckets?${params.toString()}`);
    if (!res.ok) throw new Error('Failed to fetch WHEA buckets');
    return await res.json();
}

export async function fetchWheaSignatures(sort: string = 'impact', limit: number = 50): Promise<WheaSignature[]> {
    const res = await fetch(`${getApiBase()}/api/whea/signatures?sort=${sort}&limit=${limit}`);
    if (!res.ok) throw new Error('Failed to fetch WHEA signatures');
    return await res.json();
}

export async function fetchWheaStorm(): Promise<WheaStorm> {
    const res = await fetch(`${getApiBase()}/api/whea/storms`);
    if (!res.ok) throw new Error('Failed to fetch WHEA storm status');
    return await res.json();
}

export async function generateWheaContext(fullAppendix: boolean = false): Promise<{ context: string }> {
    const res = await fetch(`${getApiBase()}/api/whea/context/generate?full_appendix=${fullAppendix}`, {
        method: 'POST'
    });
    if (!res.ok) throw new Error('Failed to generate WHEA context');
    return await res.json();
}

export async function triggerWheaIngest(): Promise<void> {
    await fetch(`${getApiBase()}/api/whea/ingest`, { method: 'POST' });
}
