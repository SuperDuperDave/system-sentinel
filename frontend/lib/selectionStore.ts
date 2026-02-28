import { create } from 'zustand';

interface SelectionState {
    selectedIds: Set<string>;
    lastSelectedId: string | null; // For shift-click range logic

    select: (id: string, modifiers?: { shift?: boolean; ctrl?: boolean }, allIdsInView?: string[]) => void;
    add: (ids: string[]) => void;
    remove: (ids: string[]) => void;
    clearSelection: () => void;
    selectAll: (ids: string[]) => void;
    isSelected: (id: string) => boolean;
}

export const useSelectionStore = create<SelectionState>((set, get) => ({
    selectedIds: new Set(),
    lastSelectedId: null,

    select: (id, modifiers = {}, allIdsInView = []) => {
        const { selectedIds, lastSelectedId } = get();
        const newSelected = new Set(modifiers.ctrl ? selectedIds : []);

        if (modifiers.shift && lastSelectedId && allIdsInView.length > 0) {
            // Range selection
            const startStr = allIdsInView.indexOf(lastSelectedId);
            const endStr = allIdsInView.indexOf(id);

            if (startStr !== -1 && endStr !== -1) {
                const start = Math.min(startStr, endStr);
                const end = Math.max(startStr, endStr);
                const range = allIdsInView.slice(start, end + 1);
                range.forEach(itemId => newSelected.add(itemId));
            } else {
                newSelected.add(id);
            }
        } else {
            // Single or Ctrl selection
            if (modifiers.ctrl) {
                if (selectedIds.has(id)) {
                    newSelected.delete(id);
                } else {
                    newSelected.add(id);
                }
            } else {
                // If just normal click, clear others and select this
                newSelected.add(id);
            }
        }

        set({ selectedIds: newSelected, lastSelectedId: id });
    },

    add: (ids) => {
        const { selectedIds } = get();
        const newSelected = new Set(selectedIds);
        ids.forEach(id => newSelected.add(id));
        set({ selectedIds: newSelected });
    },

    remove: (ids) => {
        const { selectedIds } = get();
        const newSelected = new Set(selectedIds);
        ids.forEach(id => newSelected.delete(id));
        set({ selectedIds: newSelected });
    },

    clearSelection: () => set({ selectedIds: new Set(), lastSelectedId: null }),

    selectAll: (ids) => set({ selectedIds: new Set(ids), lastSelectedId: ids[ids.length - 1] || null }),

    isSelected: (id) => get().selectedIds.has(id)
}));
