export interface SystemLog {
    TimeCreated: string;
    Id: number;
    LevelDisplayName: string;
    Message: string;
    ProviderName: string;
    Properties?: any;
    // Compatibility with new schema
    _uuid?: string;
}

export interface WheaLog {
    TimeCreated: string;
    Id: number;
    Message: string;
    RawData?: string;
    _uuid?: string;
}

export interface SentinelEvent {
    type: 'event' | 'heartbeat';
    recordId?: number;
    timeCreated: string;
    logName: string;
    provider: string;
    eventId: number;
    level: string;
    message: string;
    raw?: string;
    uniqueId?: string; // Derived frontend-side
}
