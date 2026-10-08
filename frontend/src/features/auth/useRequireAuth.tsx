import { useCallback, useEffect, useRef, useState } from "react";
import { AuthDialog } from "./AuthDialog";
import { readAuthDialogOpen, writeAuthDialogOpen } from "./authFormDraft";
import { useAuth, type AuthConfirmation } from "./AuthProvider";

type PendingAction = () => void | Promise<void>;

export function useRequireAuth() {
  const { currentUser, isCurrentAuthSession } = useAuth();
  const [authDialogOpen, setAuthDialogOpen] = useState(() => readAuthDialogOpen());
  const pendingActionRef = useRef<PendingAction | null>(null);
  const generation = useRef(0);
  const explicitlyOpened = useRef(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; generation.current++; pendingActionRef.current = null; };
  }, []);

  useEffect(() => {
    // A restored dialog may close for an existing login. An explicitly opened
    // dialog belongs to its own attempt and must wait for that attempt's callback.
    if (currentUser && !explicitlyOpened.current) {
      setAuthDialogOpen(false);
      writeAuthDialogOpen(false);
    }
  }, [currentUser]);

  const openAuthDialog = useCallback((action?: PendingAction) => {
    explicitlyOpened.current = true;
    generation.current++;
    pendingActionRef.current = action ?? null;
    setAuthDialogOpen(true);
    writeAuthDialogOpen(true);
  }, []);

  const closeAuthDialog = useCallback(() => {
    generation.current++;
    pendingActionRef.current = null;
    setAuthDialogOpen(false);
    writeAuthDialogOpen(false);
  }, []);

  const requireAuth = useCallback(
    (action: PendingAction) => {
      if (currentUser) {
        void Promise.resolve(action());
        return;
      }
      openAuthDialog(action);
    },
    [currentUser, openAuthDialog],
  );

  const handleAuthenticated = useCallback(async (confirmation: AuthConfirmation, openedGeneration: number) => {
    if (!live.current || !isCurrentAuthSession(confirmation)) return;
    // A confirmed login always closes the dialog; the pending action only runs for the attempt that opened it.
    const matches = openedGeneration === generation.current;
    generation.current++;
    const action = matches ? pendingActionRef.current : null;
    pendingActionRef.current = null;
    setAuthDialogOpen(false);
    writeAuthDialogOpen(false);
    if (action) await Promise.resolve(action());
  }, [isCurrentAuthSession]);

  const openedGeneration = generation.current;
  const authDialog = (
    <AuthDialog open={authDialogOpen} onAuthenticated={confirmation => void handleAuthenticated(confirmation, openedGeneration)} onClose={closeAuthDialog} />
  );

  return {
    requireAuth,
    openAuthDialog,
    closeAuthDialog,
    authDialog,
    isAuthenticated: Boolean(currentUser),
  };
}
