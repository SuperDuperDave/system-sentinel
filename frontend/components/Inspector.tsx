import React, { useState, useRef, useEffect } from 'react';
import { X, Copy, Check, ChevronDown, ChevronRight, Maximize2, Minimize2, PlusCircle } from 'lucide-react';
import { useContextStore } from './context/ContextStore';

interface InspectorProps {
    title?: string;
    onClose?: () => void;
    onCopy?: () => void;
    children: React.ReactNode;
    eventData?: any;
}

export const Inspector: React.FC<InspectorProps> = ({ title, onClose, onCopy, children, eventData }) => {
    const [width, setWidth] = useState(400);
    const [isResizing, setIsResizing] = useState(false);
    const [headerCopied, setHeaderCopied] = useState(false);
    const sidebarRef = useRef<HTMLDivElement>(null);
    const { addItem } = useContextStore();

    const handleHeaderCopy = () => {
        if (onCopy) {
            onCopy();
            setHeaderCopied(true);
            setTimeout(() => setHeaderCopied(false), 2000);
        }
    }

    const handleAddToContext = () => {
        if (eventData) {
            addItem({
                type: 'event',
                title: eventData.Message || eventData.ProviderName || `Event ${eventData.Id}`,
                data: eventData
            });
        }
    };

    const startResizing = React.useCallback((mouseDownEvent: React.MouseEvent) => {
        setIsResizing(true);
    }, []);

    const stopResizing = React.useCallback(() => {
        setIsResizing(false);
    }, []);

    const resize = React.useCallback(
        (mouseMoveEvent: MouseEvent) => {
            if (isResizing) {
                // Calculate new width from right edge of screen? 
                // Or just window width - mouseX. 
                // Since inspector is on right:
                const newWidth = document.body.clientWidth - mouseMoveEvent.clientX;
                if (newWidth > 300 && newWidth < 800) {
                    setWidth(newWidth);
                }
            }
        },
        [isResizing]
    );

    useEffect(() => {
        window.addEventListener("mousemove", resize);
        window.addEventListener("mouseup", stopResizing);
        return () => {
            window.removeEventListener("mousemove", resize);
            window.removeEventListener("mouseup", stopResizing);
        };
    }, [resize, stopResizing]);


    // Copy Full Content Logic
    // We need a way to grab the raw content. For now we can assume the children might pass a way to get it, 
    // or we just rely on the specific copy buttons inside. 
    // But the user asked for a "Copy Full" in title bar.
    // Since 'children' is opaque, we'll let the specific Inspector implementations handle that or 
    // just stick to the requested "JSON Code Block" copy buttons for now unless we pass the raw object to Inspector.
    // The user said: "place another copy button in the title bar... which will copy the full event details"
    // To do that, Inspector needs the data object, not just children.
    // But for now, let's just make the layout work and maybe add a generic copy if possible, 
    // or rely on the fact that Dashboard passes the object to children.
    // I will add a prop `onCopy` to Inspector so Dashboard can pass the logic.

    const handleFetchPriorEvents = async () => {
        if (!eventData || !eventData.TimeCreated) return;

        // Let the parent (Dashboard) handle the actual API call logic if complex, 
        // OR we can pass a special handler.
        // But for simplicity/dependency purity, I'll emit an event or we can just assume `useContextStore` accepts batches? 
        // Actually, the `fetchPreCrashContext` is an async action. 
        // Ideally this button calls a prop passed down: `onFetchPreCrash(timestamp)`
    };

    return (
        <div
            ref={sidebarRef}
            className="border-l border-subtle bg-bg-app h-full flex flex-col shadow-xl z-10 relative flex-shrink-0"
            style={{ width: width }}
        >
            {/* Drag Handle */}
            <div
                className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-accent/50 transition-colors z-20"
                onMouseDown={startResizing}
            />

            {title && (
                <div className="h-12 border-b border-subtle flex items-center justify-between px-4 shrink-0 bg-surface-1">
                    <span className="font-medium text-sm truncate pr-4">{title}</span>
                    <div className="flex items-center gap-2">
                        {onCopy && (
                            <button
                                onClick={handleHeaderCopy}
                                className="flex items-center gap-1.5 text-xs text-secondary hover:text-primary px-2 py-1 rounded hover:bg-surface-2 transition-colors"
                            >
                                {headerCopied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
                                <span>Copy Full</span>
                            </button>
                        )}
                        {eventData && (
                            <button
                                onClick={handleAddToContext}
                                className="flex items-center gap-1.5 text-xs text-secondary hover:text-blue-500 px-2 py-1 rounded hover:bg-surface-2 transition-colors"
                                title="Add to Context Stack"
                            >
                                <PlusCircle className="w-3.5 h-3.5" />
                                <span>Add to Context</span>
                            </button>
                        )}
                        {onClose && (
                            <button onClick={onClose} className="text-tertiary hover:text-primary p-1 rounded hover:bg-surface-2 transition-colors">
                                <X className="w-4 h-4" />
                            </button>
                        )}
                    </div>
                </div>
            )}
            <div
                className="flex-1 overflow-y-auto custom-scrollbar p-4"
                style={{ scrollbarGutter: 'stable' }}
            >
                {children}
            </div>
        </div>
    );
};

// Crash Forensics UI Component
export const CrashAnalysisCard: React.FC<{ analysis: any }> = ({ analysis }) => {
    if (!analysis) return null;
    return (
        <div className={`mb-6 p-4 rounded-lg border ${analysis.style || 'border-l-4 border-blue-500 bg-blue-500/5'}`}>
            <div className="flex items-start gap-3">
                <div className="flex-1">
                    <div className="text-xs font-bold uppercase tracking-wider opacity-70 mb-1">Quantum Diagnosis</div>
                    <div className="font-mono text-sm font-bold mb-2">{analysis.code}</div>
                    <div className="text-sm font-medium mb-2">{analysis.prognosis}</div>

                    <div className="mt-3 text-xs bg-bg-app/50 p-2 rounded border border-subtle">
                        <span className="font-bold">Mechanism:</span> {analysis.mechanism}
                    </div>
                    <div className="mt-2 text-xs bg-bg-app/50 p-2 rounded border border-subtle">
                        <span className="font-bold">Action:</span> {analysis.action}
                    </div>
                </div>
            </div>
        </div>
    );
};

export const InspectorSection: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
    <div className="mb-6">
        <div className="flex items-center justify-between mb-2">
            <div className="text-xs font-semibold text-tertiary uppercase tracking-wider">{title}</div>
        </div>
        <div className="text-sm">{children}</div>
    </div>
);

export const InspectorItem: React.FC<{ label: string; value: string | number | undefined; mono?: boolean }> = ({ label, value, mono }) => {
    const [copied, setCopied] = React.useState(false);

    const handleCopy = () => {
        if (value) {
            navigator.clipboard.writeText(String(value));
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        }
    };

    if (!value) return null;

    return (
        <div className="group flex flex-col mb-3">
            <div className="flex justify-between items-baseline mb-0.5">
                <span className="text-secondary text-xs">{label}</span>
                <button onClick={handleCopy} className="opacity-0 group-hover:opacity-100 transition-opacity text-tertiary hover:text-primary cursor-pointer">
                    {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
                </button>
            </div>
            <div className={`text-sm text-primary break-words ${mono ? 'font-mono text-xs' : ''}`}>
                {value}
            </div>
        </div>
    );
};

export const CollapsibleCode: React.FC<{ data: any; label?: string }> = ({ data, label = "JSON" }) => {
    const [isExpanded, setIsExpanded] = useState(false);
    const [copied, setCopied] = useState(false);
    const content = JSON.stringify(data, null, 2);

    const handleCopy = (e: React.MouseEvent) => {
        e.stopPropagation();
        navigator.clipboard.writeText(content);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="border border-subtle rounded-md bg-surface-active overflow-hidden">
            <div
                className="flex items-center justify-between px-3 py-2 bg-surface-1 cursor-pointer hover:bg-surface-2 transition-colors"
                onClick={() => setIsExpanded(!isExpanded)}
            >
                <div className="flex items-center gap-2 text-xs font-mono text-secondary">
                    {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                    <span>{label}</span>
                </div>
                <button
                    onClick={handleCopy}
                    className="flex items-center gap-1.5 text-xs text-secondary hover:text-primary px-2 py-0.5 rounded hover:bg-surface-2 transition-colors"
                >
                    {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
                    <span>Copy</span>
                </button>
            </div>
            {isExpanded ? (
                <div className="p-3 overflow-x-auto border-t border-subtle relative group">
                    {/* Floating copy button for long scrolls */}
                    <button
                        onClick={handleCopy}
                        className="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded z-10 text-secondary hover:text-primary hover:bg-surface-2"
                        title="Copy content"
                    >
                        {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
                    </button>
                    <pre className="text-xs font-mono text-secondary leading-relaxed">
                        {content}
                    </pre>
                </div>
            ) : (
                <div className="px-3 py-2 text-xs text-tertiary italic truncate border-t border-subtle">
                    {content.slice(0, 60)}...
                </div>
            )}
        </div>
    );
};
