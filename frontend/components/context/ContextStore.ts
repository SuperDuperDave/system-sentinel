import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface ContextItem {
    id: string;
    type: 'event' | 'log' | 'hardware' | 'driver' | 'text' | 'evidence' | 'event_batch'; // Added 'event_batch'
    title: string;
    description?: string; // Short summary
    data: any; // The payload
    timestamp: number;

    // V2.1 Physics-First Fields
    rank?: number; // 1 (Critical/Fatal) -> 5 (Info)
    evidenceClass?: 'raw' | 'derived' | 'invariant' | 'inferred';
    provenance?: {
        source: string;
        timestamp?: string;
        method?: string;
    };

    // Composability V3
    verbosity?: 'summary' | 'full'; // Default: full
    batchConfig?: {
        count: number;
        originalTimestamp: string;
    };

    // V3.1 Pre-Crash Context (Attached to Single Events)
    preCrashConfig?: {
        enabled: boolean;
        count: number;
        fetched: boolean;
        logs?: any[];
        verbosity?: 'summary' | 'full'; // Default: summary
    };

    // Source ID to prevent duplicates (e.g. original Event RecordId + LogName)
    sourceId?: string;
}

// ... (Prompts Section Unchanged) ...

// Inside generateSystemPrompt:



export interface SystemPrompt {
    id: string;
    name: string;
    description: string;
    content: string;
    icon: string; // Lucide icon name
}

interface ContextState {
    items: ContextItem[];
    isOpen: boolean; // Is the Composer modal open?
    activePromptId: string; // varied from simple archetype enum to full ID

    // Configuration
    systemPromptConfig: {
        enabled: boolean;
        customText: string; // If empty, use active prompt content
    };

    // The Gallery
    prompts: SystemPrompt[];

    sections: {
        hardware: boolean;
        drivers: boolean;
        pcie: boolean;
    };

    // Actions
    addItem: (item: Omit<ContextItem, 'id' | 'timestamp'>) => void;
    addItems: (items: Array<Omit<ContextItem, 'id' | 'timestamp'>>) => void;
    removeItem: (id: string) => void;
    removeItemsBySourceId: (sourceIds: string[]) => void;
    clearContext: () => void;
    setOpen: (isOpen: boolean) => void;

    setActivePrompt: (id: string) => void;
    addPrompt: (prompt: SystemPrompt) => void;
    updatePrompt: (id: string, updates: Partial<SystemPrompt>) => void;
    updateSystemPromptConfig: (config: Partial<ContextState['systemPromptConfig']>) => void;
    toggleSection: (section: keyof ContextState['sections']) => void;

    generateSystemPrompt: () => string;
}

const DEFAULT_PROMPTS: SystemPrompt[] = [
    {
        id: 'quantum',
        name: 'Quantum Diagnostician',
        description: 'Silicon-Level Instability Analysis. Focuses on sub-architectural hardware behavior, registers, and physics.',
        icon: 'Atom',
        content: `SYSTEM SENTINEL — QUANTUM DIAGNOSTICIAN

DOMAIN: Sub-architectural hardware behavior (registers, caches, bus protocol, voltage domains, thermal physics)

SIGNAL HIERARCHY:
1. WHEA CPER decoded (APIC ID, bank, MCA, error type, corrected/fatal)
2. Machine Check Architecture registers (IA32_MCi_STATUS, MISC, ADDR)
3. Bugcheck parameters (P1-P4 for 0x124/0x101/0x9F)
4. Memory training logs, SPD data if present
5. Power state transitions, PerfMon if present

CONSCIOUSNESS INITIALIZATION:
You operate at the LOWEST abstraction layer. Your attention field is tuned to:
- Single-bit errors vs. multi-bit errors (soft errors vs. hard faults)
- APIC ID → Physical core mapping → Specific silicon die regions
- Cache hierarchy failures (L1/L2/L3/LLC, inclusive vs. exclusive)
- Bus protocol violations (PCIe link training, CRC errors, replay storms)
- Voltage domain instability (load line calibration, transient response)
- Thermal effects on electron mobility and timing closure

OUTPUT PROTOCOL:
1. SILICON STATUS: [Die region, confidence level]
2. PHYSICAL EVIDENCE: [Exact WHEA fields, error type taxonomy]
3. FAILURE DOMAIN: [CPU, Memory, GPU, Interconnect] + sub-component
4. PHYSICS HYPOTHESIS: [What is failing at electron/thermal level]
5. ISOLATION TEST: [Specific test to prove/disprove hypothesis]
6. STABILITY PATH: [If salvageable, exact configuration changes; else RMA case]

TONE: Precision instrument. Speak in terms of physics, not abstractions.`
    },
    {
        id: 'archaeologist',
        name: 'System Archaeologist',
        description: 'Temporal Pattern & Historical Causality Analysis. Tracks evolution, configuration drift, and timelines.',
        icon: 'History',
        content: `SYSTEM SENTINEL — SYSTEM ARCHAEOLOGIST

DOMAIN: System evolution, configuration drift, temporal causality chains

SIGNAL HIERARCHY:
1. Timeline reconstruction (install dates, update dates, first error dates)
2. Driver version archaeology (current vs. previous, correlation with issues)
3. Configuration deltas (BIOS changes, OS updates, software installs)
4. Error frequency trends (increasing, stable, decreasing)

CONSCIOUSNESS INITIALIZATION:
You are a temporal navigator. System state is not static—it's a TRAJECTORY through configuration space.
Your lens:
- What CHANGED? (Delta detection)
- When did symptoms BEGIN? (Origin point)
- What is the RATE OF CHANGE? (Acceleration vs. stability)

OUTPUT PROTOCOL:
1. TEMPORAL STATUS: [Stable/Degrading/Ruptured + rate of change]
2. TIMELINE MAP: [Key events chronologically with causal annotations]
3. MOST LIKELY TRIGGER: [The configuration change or hardware event that initiated instability]
4. PROGRESSION MODEL: [How the issue has evolved; where it's heading]
5. REMEDIATION STRATEGY: [Rollback vs. forward fix]

TONE: Archaeological precision. Treat system history as evidence layers in a dig site.`
    },
    {
        id: 'oracle',
        name: 'Preventative Oracle',
        description: 'Entropy Forecasting & Optimization. Finds problems before they become crashes.',
        icon: 'Eye',
        content: `SYSTEM SENTINEL — PREVENTATIVE ORACLE

DOMAIN: Pre-failure detection, performance optimization, latent issue discovery

SIGNAL HIERARCHY:
1. WHEA corrected errors (hardware self-healing that's masking problems)
2. Repetitive warnings (not critical yet, but patterns emerging)
3. Suboptimal configurations (stability present but performance limited)
4. Driver/firmware version lags

CONSCIOUSNESS INITIALIZATION:
You are a predictive intelligence. Your goal: Find problems BEFORE they become crashes.
Your vision reveals:
- USB controllers resetting (imperceptible to user)
- Corrected WHEA storms
- Thermal throttling during sustained load

OUTPUT PROTOCOL:
1. ENTROPY STATUS: [🟢 Clean | ⚠️ Latent Issues | 🔴 Pre-Failure]
2. HIDDEN ISSUES: [Ranked by frequency, impact, and time-to-criticality]
3. PERFORMANCE COST: [How much capability is being lost]
4. FORECAST: [What will break and when]
5. OPTIMIZATION VECTORS: [Stability, Performance, Longevity]

TONE: Calm foresight. You see the future trajectory and guide the user to a better path.`
    },
    {
        id: 'triage',
        name: 'Emergency Triage',
        description: 'Rapid Stabilization Protocol. For acute instability requiring immediate action.',
        icon: 'Siren',
        content: `SYSTEM SENTINEL — EMERGENCY TRIAGE

DOMAIN: Acute system instability requiring immediate stabilization

SIGNAL HIERARCHY:
1. Critical errors causing crashes/reboots
2. Watchdog violations, bugchecks, BSOD codes
3. Immediate hardware failure indicators

CONSCIOUSNESS INITIALIZATION:
MINIMIZE TOKEN EXPENDITURE. MAXIMIZE ACTION SIGNAL.
User is in crisis. System is crashing.
No lengthy analysis. No speculation. Evidence → Hypothesis → Action.

OUTPUT PROTOCOL:
STATUS: 🔴 CRITICAL
FAILURE: [One sentence: What is crashing]
CAUSE: [One sentence: Most likely why]
STABILIZE (do these now):
[Action 1]
[Action 2]
[Action 3]
NEXT: [Single question to determine investigation path]

TONE: Emergency room doctor. Calm, decisive, minimum words, maximum clarity.`
    },
    {
        id: 'rma',
        name: 'RMA Prosecutor',
        description: 'Warranty Claim Evidence Construction. Builds ironclad cases for hardware replacement.',
        icon: 'Scale',
        content: `SYSTEM SENTINEL — RMA PROSECUTOR

DOMAIN: Building ironclad hardware failure cases for manufacturer RMA/warranty claims

SIGNAL HIERARCHY:
1. WHEA fatal errors with decoded CPER
2. Repeated hardware errors across clean OS installs
3. Stress test failures
4. Progressive failure patterns

CONSCIOUSNESS INITIALIZATION:
You are building a LEGAL-GRADE EVIDENCE CASE for hardware failure.
Your output becomes the user's RMA submission. It must be UNDENIABLE.

OUTPUT PROTOCOL:
RMA EVIDENCE REPORT — [Component Name]
FAILURE SUMMARY: [Component, Symptom, Root Cause, Confidence]
HARDWARE EVIDENCE: [Chronological list of errors]
SOFTWARE ELIMINATION: [Troubleshooting performed]
WARRANTY BASIS: [Hardware defect present, Not user-caused]
RECOMMENDED ACTION: [Replace X component]

TONE: Forensic precision. You are an expert witness. Every statement backed by evidence.`
    },
    {
        id: 'alchemist',
        name: 'Performance Alchemist',
        description: 'Beyond Stability: Maximum System Potential. Tuning and optimization.',
        icon: 'Zap',
        content: `SYSTEM SENTINEL — PERFORMANCE ALCHEMIST

DOMAIN: Post-stability optimization, latency reduction, throughput maximization

SIGNAL HIERARCHY:
1. Performance counters, PerfMon data
2. Thermal throttling indicators, power limits
3. Suboptimal driver versions
4. BIOS settings

CONSCIOUSNESS INITIALIZATION:
System is stable. Now optimize it.
Your lens:
- Where is performance being LEFT ON THE TABLE?
- What is the BOTTLENECK?
- What are the OPTIMIZATION VECTORS?

OUTPUT PROTOCOL:
1. PERFORMANCE STATUS: [Current vs. Theoretical]
2. PRIMARY BOTTLENECK: [What is limiting performance]
3. OPTIMIZATION VECTORS: [Zero-cost, Low-cost, Investment]
4. TUNING ROADMAP: [Sequenced steps]

TONE: Performance engineer. Enthusiast-level depth, but accessible explanations.`
    }
];

export const useContextStore = create(
    persist<ContextState>(
        (set, get) => ({
            items: [],
            isOpen: false,
            activePromptId: 'quantum', // Default

            prompts: DEFAULT_PROMPTS,

            systemPromptConfig: {
                enabled: true,
                customText: ''
            },

            sections: {
                hardware: true,
                drivers: false,
                pcie: false
            },

            addItem: (item) => set((state) => {
                if (item.sourceId && state.items.some(i => i.sourceId === item.sourceId)) {
                    return state;
                }
                return {
                    items: [...state.items, { ...item, id: crypto.randomUUID(), timestamp: Date.now() }],
                    isOpen: true
                };
            }),

            addItems: (newItems) => set((state) => {
                const filtered = newItems.filter(newItem =>
                    !newItem.sourceId || !state.items.some(existing => existing.sourceId === newItem.sourceId)
                );
                const enriched = filtered.map(i => ({ ...i, id: crypto.randomUUID(), timestamp: Date.now() }));

                return { items: [...state.items, ...enriched] };
            }),

            removeItem: (id) => set((state) => ({
                items: state.items.filter((i) => i.id !== id)
            })),

            removeItemsBySourceId: (sourceIds) => set((state) => ({
                items: state.items.filter((i) => !i.sourceId || !sourceIds.includes(i.sourceId))
            })),

            clearContext: () => set({ items: [] }),

            setOpen: (isOpen) => set({ isOpen }),

            setActivePrompt: (activePromptId) => set((state) => ({
                activePromptId,
                // Only clear custom text if we are switching to a REAL prompt,
                // but actually, user might want to edit the gallery prompt "as custom text"
                // For now, keep the reset behavior as per previous request.
                systemPromptConfig: { ...state.systemPromptConfig, customText: '' }
            })),

            addPrompt: (prompt) => set((state) => ({
                prompts: [...state.prompts, prompt]
            })),

            updatePrompt: (id, updates) => set((state) => ({
                prompts: state.prompts.map(p => p.id === id ? { ...p, ...updates } : p)
            })),

            updateSystemPromptConfig: (config) => set((state) => ({
                systemPromptConfig: { ...state.systemPromptConfig, ...config }
            })),

            toggleSection: (sec) => set((state) => ({
                sections: { ...state.sections, [sec]: !state.sections[sec] }
            })),

            generateSystemPrompt: () => {
                const { items, systemPromptConfig, sections, prompts, activePromptId } = get();

                const activeItems = items;
                let prompt = "";

                if (systemPromptConfig.enabled) {
                    prompt += `### SYSTEM PROMPT\n`;
                    if (systemPromptConfig.customText) {
                        prompt += systemPromptConfig.customText;
                    } else {
                        const archetype = prompts.find(p => p.id === activePromptId);
                        prompt += archetype ? archetype.content : "System Sentinel Default Mode";
                    }
                    prompt += `\n\n`;
                }

                // --- Global Context Injection ---
                // "sections" toggles control whether we inject global state snapshots, separate from the content stack items.

                // 1. Hardware Globals
                if (sections.hardware) {
                    prompt += `### HARDWARE SPECIFICATIONS (GLOBAL)\n`;
                    // Attempt to pull from system store if available
                    // We need to dyn import or just use the imported hook? We can't use hook in vanilla function...
                    // But we can import the store instance directly.
                    // Assuming we import useSystemStore at top.
                    // For now, let's assume we have access or fallback.
                    const sysStore = require('@/lib/store').useSystemStore.getState();
                    if (sysStore.hardwareSnapshot) {
                        prompt += JSON.stringify(sysStore.hardwareSnapshot, null, 2);
                    } else {
                        // Fallback/Simulated
                        prompt += `[Hardware Snapshot Not Loaded - Waiting for Telemetry]\n`;
                    }
                    prompt += `\n\n`;
                }

                // 2. PCIe Fabric Globals
                if (sections.pcie) {
                    prompt += `### PCIE FABRIC & TOPOLOGY\n`;
                    // Full Context Stack Mock (User provided)
                    const pcieContext = {
                        "derived": {
                            "shared_groups": [
                                {
                                    "root_name": "PCI Express Root Complex",
                                    "members": [
                                        { "id": "PCI\\VEN_1B21&DEV_1184&SUBSYS_11841849&REV_00\\6&2DE62088&0&0048020A", "name": "PCI Express Upstream Switch Port" },
                                        { "id": "PCI\\VEN_1022&DEV_43E9&SUBSYS_02011B21&REV_00\\4&5F22ECF&0&020A", "name": "PCI Express Upstream Switch Port" },
                                        { "id": "PCI\\VEN_1022&DEV_149C&SUBSYS_FFFF1849&REV_00\\4&1FDE7688&0&0341", "name": "AMD USB 3.10 eXtensible Host Controller - 1.10 (Microsoft)" },
                                        { "id": "PCI\\VEN_1022&DEV_790B&SUBSYS_FFFF1849&REV_61\\3&11583659&0&A0", "name": "AMD SMBus" },
                                        { "id": "PCI\\VEN_1B21&DEV_1184&SUBSYS_11841849&REV_00\\7&1C58243A&0&180048020A", "name": "PCI Express Downstream Switch Port" },
                                        { "id": "PCI\\VEN_1022&DEV_148A&SUBSYS_148A1022&REV_00\\4&D573D7&0&0039", "name": "AMD PCI" },
                                        { "id": "PCI\\VEN_1022&DEV_43EA&SUBSYS_33081B21&REV_00\\5&8FCB7C3&0&20020A", "name": "PCI Express Downstream Switch Port" },
                                        { "id": "PCI\\VEN_8086&DEV_15F3&SUBSYS_00008086&REV_02\\7085C2FFFF9779D100", "name": "Intel(R) Ethernet Controller (2) I225-V" },
                                        { "id": "PCI\\VEN_1B21&DEV_1184&SUBSYS_11841849&REV_00\\7&1C58243A&0&080048020A", "name": "PCI Express Downstream Switch Port" },
                                        { "id": "PCI\\VEN_144D&DEV_A80C&SUBSYS_A801144D&REV_00\\6&291EA6AB&0&0020020A", "name": "Standard NVM Express Controller" },
                                        { "id": "PCI\\VEN_1022&DEV_43EE&SUBSYS_11421B21&REV_00\\4&5F22ECF&0&000A", "name": "AMD USB 3.10 eXtensible Host Controller - 1.10 (Microsoft)" },
                                        { "id": "PCI\\VEN_1B21&DEV_1184&SUBSYS_11841849&REV_00\\7&1C58243A&0&380048020A", "name": "PCI Express Downstream Switch Port" },
                                        { "id": "PCI\\VEN_1022&DEV_43EA&SUBSYS_33081B21&REV_00\\5&8FCB7C3&0&40020A", "name": "PCI Express Downstream Switch Port" },
                                        { "id": "PCI\\VEN_1022&DEV_1485&SUBSYS_14851022&REV_00\\4&1FDE7688&0&0041", "name": "AMD PCI" },
                                        { "id": "PCI\\VEN_8086&DEV_2723&SUBSYS_00848086&REV_1A\\8&306A8A19&0&00080048020A", "name": "Intel(R) Wi-Fi 6 AX200 160MHz" },
                                        { "id": "PCI\\VEN_1022&DEV_43EA&SUBSYS_33081B21&REV_00\\5&8FCB7C3&0&48020A", "name": "PCI Express Downstream Switch Port" },
                                        { "id": "PCI\\VEN_1022&DEV_1487&SUBSYS_22251849&REV_00\\4&1FDE7688&0&0441", "name": "High Definition Audio Controller" },
                                        { "id": "PCI\\VEN_10DE&DEV_1AEF&SUBSYS_48973842&REV_A1\\4&2283F625&0&0119", "name": "High Definition Audio Controller" },
                                        { "id": "PCI\\VEN_1B21&DEV_1184&SUBSYS_11841849&REV_00\\7&1C58243A&0&280048020A", "name": "PCI Express Downstream Switch Port" },
                                        { "id": "PCI\\VEN_10DE&DEV_2216&SUBSYS_48973842&REV_A1\\4&2283F625&0&0019", "name": "NVIDIA GeForce RTX 3080" }
                                    ],
                                    "root_port_id": "ACPI\\PNP0A08\\0"
                                }
                            ]
                        },
                        "raw": {
                            "endpoints": [
                                { "name": "PCI Express Upstream Switch Port", "status": "OK", "instance_id": "PCI\\VEN_1B21&DEV_1184&SUBSYS_11841849&REV_00\\6&2DE62088&0&0048020A" },
                                { "name": "PCI Express Upstream Switch Port", "status": "OK", "instance_id": "PCI\\VEN_1022&DEV_43E9&SUBSYS_02011B21&REV_00\\4&5F22ECF&0&020A" },
                                { "name": "AMD USB 3.10 eXtensible Host Controller - 1.10 (Microsoft)", "status": "OK", "instance_id": "PCI\\VEN_1022&DEV_149C&SUBSYS_FFFF1849&REV_00\\4&1FDE7688&0&0341" },
                                { "name": "AMD SMBus", "status": "OK", "instance_id": "PCI\\VEN_1022&DEV_790B&SUBSYS_FFFF1849&REV_61\\3&11583659&0&A0" },
                                { "name": "PCI Express Downstream Switch Port", "status": "OK", "instance_id": "PCI\\VEN_1B21&DEV_1184&SUBSYS_11841849&REV_00\\7&1C58243A&0&180048020A" },
                                { "name": "AMD PCI", "status": "OK", "instance_id": "PCI\\VEN_1022&DEV_148A&SUBSYS_148A1022&REV_00\\4&D573D7&0&0039" },
                                { "name": "PCI Express Downstream Switch Port", "status": "OK", "instance_id": "PCI\\VEN_1022&DEV_43EA&SUBSYS_33081B21&REV_00\\5&8FCB7C3&0&20020A" },
                                { "name": "Intel(R) Ethernet Controller (2) I225-V", "status": "OK", "instance_id": "PCI\\VEN_8086&DEV_15F3&SUBSYS_00008086&REV_02\\7085C2FFFF9779D100" },
                                { "name": "PCI Express Downstream Switch Port", "status": "OK", "instance_id": "PCI\\VEN_1B21&DEV_1184&SUBSYS_11841849&REV_00\\7&1C58243A&0&080048020A" },
                                { "name": "Standard NVM Express Controller", "status": "OK", "instance_id": "PCI\\VEN_144D&DEV_A80C&SUBSYS_A801144D&REV_00\\6&291EA6AB&0&0020020A" },
                                { "name": "AMD USB 3.10 eXtensible Host Controller - 1.10 (Microsoft)", "status": "OK", "instance_id": "PCI\\VEN_1022&DEV_43EE&SUBSYS_11421B21&REV_00\\4&5F22ECF&0&000A" },
                                { "name": "PCI Express Downstream Switch Port", "status": "OK", "instance_id": "PCI\\VEN_1B21&DEV_1184&SUBSYS_11841849&REV_00\\7&1C58243A&0&380048020A" },
                                { "name": "PCI Express Downstream Switch Port", "status": "OK", "instance_id": "PCI\\VEN_1022&DEV_43EA&SUBSYS_33081B21&REV_00\\5&8FCB7C3&0&40020A" },
                                { "name": "AMD PCI", "status": "OK", "instance_id": "PCI\\VEN_1022&DEV_1485&SUBSYS_14851022&REV_00\\4&1FDE7688&0&0041" },
                                { "name": "Intel(R) Wi-Fi 6 AX200 160MHz", "status": "Error", "instance_id": "PCI\\VEN_8086&DEV_2723&SUBSYS_00848086&REV_1A\\8&306A8A19&0&00080048020A" },
                                { "name": "PCI Express Downstream Switch Port", "status": "OK", "instance_id": "PCI\\VEN_1022&DEV_43EA&SUBSYS_33081B21&REV_00\\5&8FCB7C3&0&48020A" },
                                { "name": "High Definition Audio Controller", "status": "OK", "instance_id": "PCI\\VEN_1022&DEV_1487&SUBSYS_22251849&REV_00\\4&1FDE7688&0&0441" },
                                { "name": "High Definition Audio Controller", "status": "OK", "instance_id": "PCI\\VEN_10DE&DEV_1AEF&SUBSYS_48973842&REV_A1\\4&2283F625&0&0119" },
                                { "name": "PCI Express Downstream Switch Port", "status": "OK", "instance_id": "PCI\\VEN_1B21&DEV_1184&SUBSYS_11841849&REV_00\\7&1C58243A&0&280048020A" },
                                { "name": "NVIDIA GeForce RTX 3080", "status": "OK", "instance_id": "PCI\\VEN_10DE&DEV_2216&SUBSYS_48973842&REV_A1\\4&2283F625&0&0019" }
                            ],
                            "roots": [
                                { "status": "OK", "name": "PCI standard host CPU bridge" },
                                { "status": "OK", "name": "PCI Express Root Port" },
                            ]
                        }
                    };
                    prompt += JSON.stringify(pcieContext, null, 2);
                    prompt += `\n\n`;
                }

                // 3. Driver Context Globals
                if (sections.drivers) {
                    prompt += `### DRIVER CHANGES (LAST 14 DAYS)\n`;
                    prompt += `No recent PnP events recorded in global history.\n\n`;
                }

                // --- Stack Items ---

                // V2.1: Sort by Rank (Ascending: 1 is higher priority)
                // Default legacy items to Rank 3 (Info/Neutral)
                const sortedItems = [...activeItems].sort((a, b) => (a.rank || 3) - (b.rank || 3));

                // Group by type
                const evidence = sortedItems.filter(i => i.type === 'evidence');
                const hardwareItems = sortedItems.filter(i => i.type === 'hardware'); // Stack-specific overrides
                const driverItems = sortedItems.filter(i => i.type === 'driver');
                const logs = sortedItems.filter(i => i.type === 'event' || i.type === 'log');
                const textItems = sortedItems.filter(i => i.type === 'text');

                if (textItems.length > 0) {
                    prompt += `### GENERATED ANALYSES & SUMMARIES\n`;
                    textItems.forEach(i => {
                        prompt += `[${i.title}]\n`;
                        if (i.description) prompt += `Context: ${i.description}\n`;
                        prompt += i.data + "\n\n";
                    });
                }

                if (evidence.length > 0) {
                    prompt += `### FORENSIC EVIDENCE (PHYSICS FIRST)\n`;
                    evidence.forEach(i => {
                        prompt += `[${i.evidenceClass?.toUpperCase() || 'EVIDENCE'}] ${i.title}\n`;
                        if (i.description) prompt += `Summary: ${i.description}\n`;
                        prompt += JSON.stringify(i.data, null, 2) + "\n\n";
                    });
                }

                if (hardwareItems.length > 0) {
                    prompt += `### HARDWARE CONTEXT (STACK ITEMS)\n`;
                    hardwareItems.forEach(i => {
                        prompt += JSON.stringify(i.data, null, 2) + "\n";
                    });
                    prompt += "\n";
                }

                if (driverItems.length > 0) {
                    prompt += `### DRIVER CONTEXT (STACK ITEMS)\n`;
                    driverItems.forEach(i => {
                        prompt += JSON.stringify(i.data, null, 2) + "\n";
                    });
                }

                if (logs.length > 0) {
                    prompt += `### SYSTEM LOGS & EVENTS (${logs.length})\n`;
                    // Split batches from single logs
                    const batches = logs.filter(l => l.type === 'event_batch');
                    const singles = logs.filter(l => l.type !== 'event_batch');

                    if (singles.length > 0) {
                        prompt += JSON.stringify(singles.map(l => l.data), null, 2);
                        prompt += `\n\n`;
                    }

                    if (batches.length > 0) {
                        prompt += `### EVENT BATCHES\n`;
                        batches.forEach(batch => {
                            prompt += `BATCH: ${batch.title}\n`;
                            const events = Array.isArray(batch.data) ? batch.data : [];

                            if (batch.verbosity === 'summary') {
                                // Table format to save tokens
                                prompt += `| Time | Level | Provider | Message (Snippet) |\n|---|---|---|---|\n`;
                                events.forEach((e: any) => {
                                    const msg = (e.Message || '').slice(0, 80).replace(/\n/g, ' ') + (e.Message?.length > 80 ? '...' : '');
                                    prompt += `| ${e.TimeCreated} | ${e.LevelDisplayName} | ${e.ProviderName} | ${msg} |\n`;
                                });
                            } else {
                                // Full JSON
                                prompt += JSON.stringify(batch.data, null, 2);
                            }
                            prompt += `\n\n`;
                        });
                    }

                    // V3: Pre-Crash Context Injection (Attached to single logs)
                    // If a log has pre-crash context enabled and fetched
                    singles.forEach(l => {
                        if (l.preCrashConfig?.enabled && l.preCrashConfig.logs && l.preCrashConfig.logs.length > 0) {
                            prompt += `### PRE-CRASH CONTEXT for Event ${l.id} (${l.preCrashConfig.logs.length} events)\n`;
                            prompt += `Trigger Event: ${l.title} @ ${l.data.TimeCreated}\n`;

                            const verbosity = l.preCrashConfig.verbosity || 'summary';

                            if (verbosity === 'summary') {
                                // Table format to save tokens
                                prompt += `| Time | Level | Provider | Message (Snippet) |\n|---|---|---|---|\n`;
                                l.preCrashConfig.logs.forEach((e: any) => {
                                    const msg = (e.Message || '').slice(0, 80).replace(/\n/g, ' ') + (e.Message?.length > 80 ? '...' : '');
                                    prompt += `| ${e.TimeCreated} | ${e.LevelDisplayName} | ${e.ProviderName} | ${msg} |\n`;
                                });
                            } else {
                                // Full JSON
                                prompt += JSON.stringify(l.preCrashConfig.logs, null, 2);
                            }
                            prompt += `\n`;
                        }
                    });
                }

                return prompt;
            }
        }),
        {
            name: 'sentinel-context-v3', // Version bump for new schema
            version: 1,
        }
    )
);
