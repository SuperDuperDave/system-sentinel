import React, { useState } from 'react';
import { Layers, X, Copy, Trash2, ChevronUp, ChevronDown, FileText, Cpu, Activity } from 'lucide-react';
import { useContextStore, ContextItem } from './ContextStore';

export const ContextTray: React.FC = () => {
    const { items, isOpen, removeItem, clearContext, setOpen, generateSystemPrompt } = useContextStore();
    const [copied, setCopied] = useState(false);

    if (items.length === 0 && !isOpen) return null;

    const handleCopy = () => {
        const prompt = generateSystemPrompt();
        navigator.clipboard.writeText(prompt);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const getIcon = (type: string) => {
        switch (type) {
            case 'hardware': return <Cpu className="w-4 h-4 text-purple-400" />;
            case 'event': return <Activity className="w-4 h-4 text-orange-400" />;
            default: return <FileText className="w-4 h-4 text-blue-400" />;
        }
    };

    return (
        <div className={`fixed bottom-6 right-6 z-50 flex flex-col items-end transition-all duration-300 ${isOpen ? 'w-96' : 'w-auto'}`}>

            {/* Expanded Tray */}
            {isOpen && (
                <div className="w-full bg-surface-1 border border-subtle rounded-lg shadow-2xl overflow-hidden mb-3 animate-in slide-in-from-bottom-4 fade-in duration-200">
                    <div className="bg-surface-2 px-4 py-3 flex items-center justify-between border-b border-subtle">
                        <div className="flex items-center gap-2">
                            <Layers className="w-4 h-4 text-primary" />
                            <span className="font-bold text-sm text-primary">Context Stack</span>
                            <span className="bg-blue-600 text-white text-[10px] px-1.5 py-0.5 rounded-full">{items.length}</span>
                        </div>
                        <div className="flex items-center gap-1">
                            <button onClick={clearContext} className="p-1.5 hover:bg-surface-3 rounded text-tertiary hover:text-red-400 transition-colors" title="Clear All">
                                <Trash2 className="w-3.5 h-3.5" />
                            </button>
                            <button onClick={() => setOpen(false)} className="p-1.5 hover:bg-surface-3 rounded text-tertiary hover:text-primary transition-colors">
                                <ChevronDown className="w-4 h-4" />
                            </button>
                        </div>
                    </div>

                    <div className="max-h-[60vh] overflow-y-auto custom-scrollbar p-2 flex flex-col gap-2">
                        {items.length === 0 ? (
                            <div className="p-8 text-center text-tertiary text-sm italic">
                                Stack is empty.<br />Add items from the inspector or dashboard.
                            </div>
                        ) : (
                            items.map((item) => (
                                <div key={item.id} className="bg-bg-app border border-subtle rounded p-3 flex gap-3 group relative hover:border-blue-500/30 transition-colors">
                                    <div className="mt-0.5">{getIcon(item.type)}</div>
                                    <div className="flex-1 min-w-0">
                                        <div className="text-sm font-medium text-primary truncate pr-4">{item.title}</div>
                                        <div className="text-xs text-secondary truncate font-mono mt-0.5">
                                            {JSON.stringify(item.data).slice(0, 60)}...
                                        </div>
                                    </div>
                                    <button
                                        onClick={() => removeItem(item.id)}
                                        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 p-1 hover:bg-surface-2 rounded text-tertiary hover:text-red-400 transition-all"
                                    >
                                        <X className="w-3 h-3" />
                                    </button>
                                </div>
                            ))
                        )}
                    </div>

                    <div className="p-3 border-t border-subtle bg-surface-2">
                        <button
                            onClick={handleCopy}
                            disabled={items.length === 0}
                            className={`w-full py-2 px-4 rounded-md font-medium text-sm flex items-center justify-center gap-2 transition-all
                                ${items.length === 0 ? 'bg-surface-3 text-tertiary cursor-not-allowed' :
                                    copied ? 'bg-emerald-600 text-white' : 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20'}
                            `}
                        >
                            {copied ? (
                                <><span>Copied to Clipboard!</span></>
                            ) : (
                                <><Copy className="w-4 h-4" /><span>Copy Context for AI</span></>
                            )}
                        </button>
                    </div>
                </div>
            )}

            {/* Floating Toggle Button */}
            {!isOpen && items.length > 0 && (
                <button
                    onClick={() => setOpen(true)}
                    className="flex items-center gap-2 bg-blue-600 hover:bg-blue-500 text-white px-4 py-3 rounded-full shadow-xl hover:shadow-2xl transition-all animate-in zoom-in duration-300 group"
                >
                    <Layers className="w-5 h-5 group-hover:scale-110 transition-transform" />
                    <span className="font-bold">{items.length}</span>
                </button>
            )}
        </div>
    );
};
