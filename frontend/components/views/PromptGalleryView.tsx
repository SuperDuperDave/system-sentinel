import React, { useState, useEffect } from 'react';
import { useContextStore } from '../context/ContextStore';
import {
    Zap, Activity, Shield, Stethoscope, Search, AlertOctagon,
    BookOpen, Scale, X, Check, Save, Plus, Edit2
} from 'lucide-react';
import { SystemPrompt } from '../context/ContextStore';

const ICON_MAP: Record<string, any> = {
    Zap, Activity, Shield, Stethoscope, Search, AlertOctagon, BookOpen, Scale
};

export const PromptGalleryView: React.FC = () => {
    const { prompts, activePromptId, setActivePrompt, updatePrompt, addPrompt } = useContextStore();
    const [selectedPrompt, setSelectedPrompt] = useState<SystemPrompt | null>(null);
    const [isEditing, setIsEditing] = useState(false);
    const [editContent, setEditContent] = useState('');
    const [isCreating, setIsCreating] = useState(false);

    // New Prompt State
    const [newPromptName, setNewPromptName] = useState('');
    const [newPromptDesc, setNewPromptDesc] = useState('');
    const [newPromptContent, setNewPromptContent] = useState('');

    const handleCardClick = (prompt: SystemPrompt) => {
        setSelectedPrompt(prompt);
        setEditContent(prompt.content);
        setIsEditing(false);
        setIsCreating(false);
    };

    const handleActivate = () => {
        if (selectedPrompt) {
            setActivePrompt(selectedPrompt.id);
            setSelectedPrompt(null);
        }
    };

    const handleSaveEdit = () => {
        if (selectedPrompt) {
            updatePrompt(selectedPrompt.id, { content: editContent });
            setSelectedPrompt({ ...selectedPrompt, content: editContent });
            setIsEditing(false);
        }
    };

    const handleCreateNew = () => {
        const newPrompt: SystemPrompt = {
            id: `custom - ${Date.now()} `,
            name: newPromptName || 'New Archetype',
            description: newPromptDesc || 'Custom system prompt',
            content: newPromptContent,
            icon: 'Zap'
        };
        addPrompt(newPrompt);
        setIsCreating(false);
        setNewPromptName('');
        setNewPromptDesc('');
        setNewPromptContent('');
    };

    const openCreateModal = () => {
        setIsCreating(true);
        setSelectedPrompt(null);
    };

    return (
        <div className="h-full flex flex-col p-8 overflow-y-auto custom-scrollbar">
            <div className="flex justify-between items-center mb-8">
                <div>
                    <h1 className="text-2xl font-bold text-primary mb-2">System Prompt Gallery</h1>
                    <p className="text-secondary max-w-2xl">
                        Select an AI archetype to specialize the Sentinel's analysis capabilities.
                        Each prompt is tailored for specific debugging scenarios.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6 pb-20">
                {/* Create New Card */}
                <button
                    onClick={openCreateModal}
                    className="flex flex-col items-center justify-center p-6 rounded-xl border-2 border-dashed border-subtle hover:border-blue-500/50 hover:bg-surface-2 transition-all group min-h-[200px]"
                >
                    <div className="w-12 h-12 rounded-full bg-surface-2 group-hover:bg-blue-500/10 flex items-center justify-center mb-4 transition-colors">
                        <Plus className="w-6 h-6 text-secondary group-hover:text-blue-500" />
                    </div>
                    <h3 className="text-lg font-bold text-primary mb-1">Create New</h3>
                    <p className="text-sm text-tertiary text-center">Define a custom archetype</p>
                </button>

                {prompts.map((prompt) => {
                    const Icon = ICON_MAP[prompt.icon] || Zap;
                    const isActive = activePromptId === prompt.id;

                    return (
                        <div
                            key={prompt.id}
                            onClick={() => handleCardClick(prompt)}
                            className={`
                                relative flex flex-col p-6 rounded-xl border transition-all cursor-pointer group
                                ${isActive
                                    ? 'bg-blue-500/5 border-blue-500/30'
                                    : 'bg-surface-1 border-subtle hover:border-primary/30 hover:shadow-lg'
                                }
`}
                        >
                            {isActive && (
                                <div className="absolute top-4 right-4">
                                    <span className="flex items-center gap-1 text-xs font-bold text-blue-400 bg-blue-500/10 px-2 py-1 rounded-full">
                                        <Check className="w-3 h-3" />
                                        Active
                                    </span>
                                </div>
                            )}

                            <div className={`w-12 h-12 rounded-lg flex items-center justify-center mb-4 ${isActive ? 'bg-blue-500/20 text-blue-400' : 'bg-surface-2 text-secondary group-hover:text-primary transition-colors'} `}>
                                <Icon className="w-6 h-6" />
                            </div>

                            <h3 className="text-lg font-bold text-primary mb-2">{prompt.name}</h3>
                            <p className="text-sm text-secondary line-clamp-3 mb-4 flex-1">
                                {prompt.description}
                            </p>

                            <div className="text-xs text-tertiary font-mono truncate opacity-60">
                                ID: {prompt.id}
                            </div>
                        </div>
                    );
                })}
            </div>

            {/* Detail / Edit Modal */}
            {selectedPrompt && (
                <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setSelectedPrompt(null)}>
                    <div className="bg-bg-app border border-subtle w-full max-w-2xl max-h-[85vh] rounded-2xl shadow-2xl flex flex-col overflow-hidden animate-in zoom-in-95 duration-200" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between p-6 border-b border-subtle bg-surface-1">
                            <div className="flex items-center gap-4">
                                <div className="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center text-blue-400">
                                    {React.createElement(ICON_MAP[selectedPrompt.icon] || Zap, { className: "w-6 h-6" })}
                                </div>
                                <div>
                                    <h2 className="text-xl font-bold text-primary">{selectedPrompt.name}</h2>
                                    <p className="text-sm text-secondary">{selectedPrompt.description}</p>
                                </div>
                            </div>
                            <button onClick={() => setSelectedPrompt(null)} className="text-secondary hover:text-primary p-2">
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        <div className="flex-1 overflow-y-auto p-6 bg-surface-2 relative">
                            {/* Toolbar */}
                            {!isEditing && (
                                <button
                                    onClick={() => setIsEditing(true)}
                                    className="absolute top-6 right-6 p-2 bg-surface-1 hover:bg-surface-3 rounded-md text-secondary hover:text-primary transition-colors z-10"
                                    title="Edit Prompt"
                                >
                                    <Edit2 className="w-4 h-4" />
                                </button>
                            )}

                            {isEditing ? (
                                <textarea
                                    value={editContent}
                                    onChange={(e) => setEditContent(e.target.value)}
                                    className="w-full h-full min-h-[400px] bg-bg-app p-4 rounded-lg font-mono text-sm text-primary border border-subtle focus:border-blue-500 outline-none resize-none"
                                    placeholder="Enter system prompt..."
                                />
                            ) : (
                                <div className="prose prose-invert prose-sm max-w-none">
                                    <pre className="whitespace-pre-wrap font-mono text-sm text-secondary bg-bg-app p-4 rounded-lg border border-subtle">
                                        {selectedPrompt.content}
                                    </pre>
                                </div>
                            )}
                        </div>

                        <div className="p-6 border-t border-subtle bg-surface-1 flex justify-end gap-3">
                            <button
                                onClick={() => setSelectedPrompt(null)}
                                className="px-4 py-2 text-sm font-medium text-secondary hover:text-primary transition-colors"
                            >
                                Close
                            </button>

                            {isEditing ? (
                                <button
                                    onClick={handleSaveEdit}
                                    className="flex items-center gap-2 px-6 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg font-medium transition-colors"
                                >
                                    <Save className="w-4 h-4" />
                                    Save Changes
                                </button>
                            ) : (
                                <button
                                    onClick={handleActivate}
                                    disabled={activePromptId === selectedPrompt.id}
                                    className={`
                                        flex items-center gap-2 px-6 py-2 rounded-lg font-medium transition-colors
                                        ${activePromptId === selectedPrompt.id
                                            ? 'bg-surface-2 text-tertiary cursor-not-allowed'
                                            : 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-500/20'
                                        }
`}
                                >
                                    {activePromptId === selectedPrompt.id ? (
                                        <>
                                            <Check className="w-4 h-4" />
                                            Active
                                        </>
                                    ) : (
                                        <>
                                            <Zap className="w-4 h-4" />
                                            Activate Archetype
                                        </>
                                    )}
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            )}

            {/* Create New Modal */}
            {isCreating && (
                <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setIsCreating(false)}>
                    <div className="bg-bg-app border border-subtle w-full max-w-xl rounded-2xl shadow-2xl flex flex-col animate-in zoom-in-95 duration-200" onClick={e => e.stopPropagation()}>
                        <div className="p-6 border-b border-subtle">
                            <h2 className="text-xl font-bold text-primary">Create New Archetype</h2>
                        </div>

                        <div className="p-6 flex flex-col gap-4">
                            <div>
                                <label className="block text-xs font-bold text-secondary uppercase mb-1">Name</label>
                                <input
                                    value={newPromptName}
                                    onChange={e => setNewPromptName(e.target.value)}
                                    className="w-full bg-surface-1 text-primary border border-subtle rounded-md px-3 py-2 text-sm outline-none focus:border-blue-500"
                                    placeholder="e.g. Network Specialist"
                                />
                            </div>
                            <div>
                                <label className="block text-xs font-bold text-secondary uppercase mb-1">Description</label>
                                <input
                                    value={newPromptDesc}
                                    onChange={e => setNewPromptDesc(e.target.value)}
                                    className="w-full bg-surface-1 text-primary border border-subtle rounded-md px-3 py-2 text-sm outline-none focus:border-blue-500"
                                    placeholder="Short description of the role"
                                />
                            </div>
                            <div>
                                <label className="block text-xs font-bold text-secondary uppercase mb-1">Prompt Content</label>
                                <textarea
                                    value={newPromptContent}
                                    onChange={e => setNewPromptContent(e.target.value)}
                                    className="w-full bg-surface-1 text-primary border border-subtle rounded-md px-3 py-2 text-sm font-mono h-40 outline-none focus:border-blue-500 resize-none"
                                    placeholder="You are a..."
                                />
                            </div>
                        </div>

                        <div className="p-6 border-t border-subtle flex justify-end gap-3">
                            <button
                                onClick={() => setIsCreating(false)}
                                className="px-4 py-2 text-sm font-medium text-secondary hover:text-primary transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleCreateNew}
                                disabled={!newPromptName}
                                className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                Create Prompt
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};
