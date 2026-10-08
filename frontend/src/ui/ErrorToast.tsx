import { useEffect } from "react";
import "./Toast";
import { toastError, type ToastAction } from "./toastStore";

/** Drop-in for an inline error paragraph: shows the message as a snackbar whenever it changes, renders nothing itself. */
export function ErrorToast({ message, action }: { message?: string | null; action?: ToastAction }) {
  useEffect(() => { if (message) toastError(message, action); }, [message]);
  return null;
}
