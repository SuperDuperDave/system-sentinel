import { NewItem } from './stack';

/**
 * The one control every view uses to hand evidence to the stack. The contract is the `NewItem`
 * it posts; the behaviour (post, confirm in place, refuse a duplicate) is filled in phase 2 with
 * the Stack view. Until then it renders nothing, so no view shows a control that does not work.
 */
export function AddToStack({ item, label = 'Add to stack' }: { item: NewItem; label?: string }) {
  void item;
  void label;
  return null;
}
