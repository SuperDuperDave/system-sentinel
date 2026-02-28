import React from 'react';
import { Plus, X, Layers } from 'lucide-react';
import { useContextStore } from '../../../context/ContextStore';

interface ContextButtonProps {
    title: string;
    description: string;
    data: any;
    sourceId: string;
    evidenceClass?: 'raw' | 'derived' | 'invariant' | 'inferred';
    icon?: React.ElementType;
    label?: string;
    className?: string;
}

export const ContextButton: React.FC<ContextButtonProps> = ({
    title,
    description,
    data,
    sourceId,
    evidenceClass = 'invariant',
    icon: Icon = Layers,
    label = "Add Context",
    className = ""
}) => {
    const { items, addItem, removeItemsBySourceId } = useContextStore();

    const isAdded = items.some(i => i.sourceId === sourceId);

    const handleClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        if (isAdded) {
            removeItemsBySourceId([sourceId]);
        } else {
            addItem({
                type: 'evidence',
                title,
                description,
                data,
                evidenceClass,
                rank: 5,
                sourceId
            });
        }
    };

    return (
        <button
            onClick={handleClick}
            className={`
                flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium border transition-all
                ${isAdded
                    ? 'bg-blue-500/10 border-blue-500 text-blue-400 hover:bg-blue-500/20'
                    : 'bg-surface-2 border-subtle text-secondary hover:text-primary hover:border-primary/50'}
                ${className}
            `}
        >
            {isAdded ? <X size={12} /> : <Plus size={12} />}
            {isAdded ? "Added" : label}
        </button>
    );
};
