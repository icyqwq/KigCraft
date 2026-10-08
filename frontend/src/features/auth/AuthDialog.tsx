import { type MouseEvent } from "react";
import { Box } from "../../ui/mui";
import { AuthPage } from "./AuthPage";
import type { AuthConfirmation } from "./AuthProvider";

type AuthDialogProps = {
  open: boolean;
  onAuthenticated: (confirmation: AuthConfirmation) => void;
  onClose: () => void;
};

function handleBackdropClick(event: MouseEvent<HTMLDivElement>, onClose: () => void) {
  if (event.target === event.currentTarget) {
    onClose();
  }
}

export function AuthDialog({ open, onAuthenticated, onClose }: AuthDialogProps) {
  if (!open) return null;

  return (
    <Box
      px={2}
      py={4}
      style={{
        background: "rgba(0, 0, 0, 0.52)",
        display: "grid",
        inset: 0,
        placeItems: "center",
        position: "fixed",
        zIndex: 1300,
      }}
      onMouseDown={(event: MouseEvent<HTMLDivElement>) => handleBackdropClick(event, onClose)}
    >
      <Box maw={440} w="100%">
        <AuthPage variant="dialog" onAuthenticated={onAuthenticated} />
      </Box>
    </Box>
  );
}
