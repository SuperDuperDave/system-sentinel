import React from 'react';
import { Layers, Plus, Minus, X } from 'lucide-react';
import { useContextStore } from './ContextStore';
import { useSelectionStore } from '@/lib/selectionStore';
import { useSystemStore } from '@/lib/store';

interface SmartContextControlProps {
    availableItems?: any[];
    selectedItems?: any[]; // Allow passing resolved objects directly
}

export const SmartContextControl: React.FC<SmartContextControlProps> = ({ availableItems = [], selectedItems }) => {
    const { items, setOpen, addItems, removeItemsBySourceId } = useContextStore();
    const { selectedIds, clearSelection } = useSelectionStore();
    // const systemEvents = useSystemStore(state => state.events); // Deprecated in favor of passed items

    // Check if selected items are already in context
    // We need to map selected IDs to actual objects to check for existence
    // Since uniqueId = sourceId in our logic

    const selectedCount = selectedIds.size;
    const selectionArray = Array.from(selectedIds);

    // Count how many selected items are ALREADY in the context
    const existingInContext = selectionArray.filter(id =>
        items.some(item => item.sourceId === id)
    ).length;

    const allSelectedAreInContext = selectedCount > 0 && existingInContext === selectedCount;
    // const someSelectedAreInContext = selectedCount > 0 && existingInContext > 0 && !allSelectedAreInContext;

    const handleAction = () => {
        if (selectedCount === 0) {
            setOpen(true);
            return;
        }

        // Logic: 
        // 1. If ALL selected are in context -> Remove them
        // 2. If SOME or NONE are in context -> Add the missing ones (Deduplication handled by store, but we can filter here too)

        if (allSelectedAreInContext) {
            removeItemsBySourceId(selectionArray);
        } else {
            // Find objects to add
            // We use the passed availableItems (which respects filters/legacy logic from Dashboard)

            const objectsToAdd = availableItems
                .filter(e => {
                    const id = e.uniqueId || e._uuid;
                    return id && selectedIds.has(id);
                })
                .map(e => ({
                    type: 'event' as const,
                    title: e.Message || e.ProviderName || `Event ${e.Id}`,
                    data: e,
                    sourceId: e.uniqueId || e._uuid
                }));

            if (objectsToAdd.length > 0) {
                addItems(objectsToAdd);
                // We do NOT clear selection here, per user request to keep context
            }
        }
    };

    // Derived UI State
    let actionLabel = "";
    let actionIcon = null;
    let actionColorClass = "";
    let showActionButton = false;

    if (selectedCount > 0) {
        showActionButton = true;
        if (allSelectedAreInContext) {
            actionLabel = `Remove ${selectedCount} from Stack`;
            actionIcon = <Minus className="w-5 h-5" />;
            // actionColorClass unused now, moving to inline conditional
        } else {
            const countToAdd = selectedCount - existingInContext;
            actionLabel = existingInContext > 0
                ? `Update Stack (+${countToAdd} New)`
                : `Add ${selectedCount} to Context`;
            actionIcon = <Plus className="w-5 h-5" />;
        }
    }

    return (
        <div className="fixed bottom-6 right-6 z-50 flex items-center gap-3 animate-in zoom-in duration-300 pointer-events-none">
            {showActionButton && (
                <button
                    onClick={handleAction}
                    className={`pointer-events-auto flex items-center justify-center w-10 h-10 rounded-full shadow-lg border transition-all hover:scale-105 active:scale-95 ${allSelectedAreInContext
                        ? 'bg-red-500/10 border-red-500/20 text-red-500 hover:bg-red-500/20' // Remove: Subtle Red
                        : 'bg-surface-1 border-subtle text-blue-500 hover:bg-surface-2 hover:border-blue-500' // Add: Subtle Blue
                        }`}
                    title={actionLabel}
                >
                    {actionIcon}
                </button>
            )}

            {/* Context Stack Pill (Always Visible, Pure Navigation) */}
            <button
                onClick={() => setOpen(true)}
                className={`
                    pointer-events-auto flex items-center gap-3 px-5 py-3 rounded-full shadow-xl transition-all border border-white/10
                    ${items.length > 0 ? 'bg-surface-active text-primary hover:bg-surface-2' : 'bg-surface-1 text-secondary hover:bg-surface-2'}
                `}
            >
                <Layers className={`w-5 h-5 ${items.length > 0 ? 'text-blue-500' : 'text-tertiary'}`} />
                <span className="font-medium">Stack ({items.length})</span>
            </button>
        </div>
    );
};

// Helper for red trash icon to distinguish from standard Lucide import if needed
const TrashIcon = ({ className }: { className?: string }) => (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
        <path d="M3 6h18" /><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" /><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
    </svg>
);
