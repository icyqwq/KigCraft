export type ToastAction = { label: string; onClick: () => void };
export type ToastItem = { id: number; message: string; kind: "info" | "error"; action?: ToastAction; leaving?: boolean };
const DURATION_MS = { info: 2000, error: 6000 }, EXIT_MS = 150;
let items: ToastItem[] = [];
let sequence = 0;
let mountHost: (() => void) | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach(listener => listener());
export const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const snapshot = () => items;
/** The host registers itself on import so a toast can be shown from anywhere without mounting a component first. */
export const registerHostMounter = (mount: () => void) => { mountHost = mount; };

function push(message: string, kind: ToastItem["kind"], action?: ToastAction) {
  mountHost?.();
  // The same message already on screen is refreshed rather than stacked.
  if (items.some(item => item.message === message && item.kind === kind && !item.leaving)) return;
  const id = ++sequence, duration = DURATION_MS[kind];
  items = [...items.slice(-2), { id, message, kind, action }];
  emit();
  setTimeout(() => { items = items.map(item => item.id === id ? { ...item, leaving: true } : item); emit(); }, duration - EXIT_MS);
  setTimeout(() => { items = items.filter(item => item.id !== id); emit(); }, duration);
}

export function dismissToast(id: number) { items = items.filter(item => item.id !== id); emit(); }

/** Test helper: drop every toast. */
export function clearToasts() { items = []; emit(); }

/** A short confirmation (2 s). */
export const toast = (message: string) => push(message, "info");
/** A failure, shown as a snackbar (6 s) with an optional action such as retry. Errors are never embedded in the page. */
export const toastError = (message: string, action?: ToastAction) => push(message, "error", action);
