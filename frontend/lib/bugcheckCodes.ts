export interface BugCheckInfo {
    code: string;
    prognosis: string; // The "Quantum" analysis (Physics/Hardware level)
    mechanism: string; // What actually happened (Driver/Timeout/Memory)
    action: string;    // Recommended next step
}

export const BUGCHECK_KNOWLEDGE_BASE: Record<string, BugCheckInfo> = {
    // DPC_WATCHDOG_VIOLATION
    '0x133': {
        code: '0x00000133',
        prognosis: 'QUANTUM HALT: Electron flow stagnation. A high-priority kernel task refused to yield the processor, causing a temporal violation of the system clock interrupt.',
        mechanism: 'DPC (Deferred Procedure Call) took longer than 10 seconds. Usually caused by SSD firmware hangs, WiFi drivers, or GPU latch-up.',
        action: 'Isolate storage subsystem. Check for SSD firmware updates. If NVMe, inspect thermal throttling behavior.'
    },
    // WHEA_UNCORRECTABLE_ERROR
    '0x124': {
        code: '0x00000124',
        prognosis: 'SILICON RUPTURE: Hardware assertion failure. The CPU itself detected a voltage/timing violation in its internal logic banks or cache hierarchy.',
        mechanism: 'Machine Check Exception (MCE) directly from the processor. Not a driver bug. This is physical hardware instability.',
        action: 'Decode the CPER record immediately. Check Voltage (Vcore), Load Line Calibration, and specific core affinity.'
    },
    // VIDEO_TDR_FAILURE
    '0x116': {
        code: '0x00000116',
        prognosis: 'PHOTONIC COLLAPSE: Graphics pipeline froze. The GPU stopped responding to commands, and the kernel reset the display driver to recover the desktop.',
        mechanism: 'TDR (Timeout Detection and Recovery). GPU stuck in an infinite loop or hung on a memory fetch.',
        action: 'Inspect GPU voltage curves (undervolt instability?) and VRAM temperatures. Roll back display driver if recent update.'
    },
    // CLOCK_WATCHDOG_TIMEOUT
    '0x101': {
        code: '0x00000101',
        prognosis: 'TEMPORAL DESYNC: Multi-core coherence failure. One CPU core stopped acknowledging interrupts from other cores, effectively exiting the collective reality of the OS.',
        mechanism: 'Processor core hang. Often intermittent voltage droop or excessive overclocking.',
        action: 'Increase Vcore slightly. Check for "lazy" cores in per-core overclocking configurations.'
    },
    // DRIVER_POWER_STATE_FAILURE
    '0x9f': {
        code: '0x0000009F',
        prognosis: 'ENERGY STATE LIMBO: A device refused to wake up or go to sleep. It trapped the kernel in a transition state, forcing a bugcheck to prevent inconsistent power topology.',
        mechanism: 'IRP (I/O Request Packet) stuck in a device driver during P-state transition.',
        action: 'Identify the specific device stack (usually WiFi, Bluetooth, or legacy PCI cards) in the dump analysis.'
    }
};

export const MOOD_STYLE = {
    default: 'border-l-4 border-blue-500 bg-blue-500/5',
    critical: 'border-l-4 border-red-500 bg-red-500/5',
    quantum: 'border-l-4 border-purple-500 bg-purple-500/5'
};
