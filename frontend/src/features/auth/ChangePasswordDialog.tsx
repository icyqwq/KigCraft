import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, changePassword } from "../../api/client";
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextInput } from "../../ui/mui";

const CHANGE_PASSWORD_ERROR_KEYS = {
  invalid_current_password: "errors.invalid_current_password",
  password_unchanged: "errors.password_unchanged",
  login_rate_limited: "errors.login_rate_limited",
} as const;

type ChangePasswordErrorDetail = keyof typeof CHANGE_PASSWORD_ERROR_KEYS;

function formatChangePasswordError(
  error: unknown,
  fallback: string,
  t: (key: string, options?: Record<string, string | number>) => string,
) {
  if (error instanceof ApiError && typeof error.detail === "string") {
    if (Object.hasOwn(CHANGE_PASSWORD_ERROR_KEYS, error.detail)) {
      return t(CHANGE_PASSWORD_ERROR_KEYS[error.detail as ChangePasswordErrorDetail]);
    }
  }
  return fallback;
}

export function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const isMounted = useRef(true);

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(false);

    if (newPassword !== confirmPassword) {
      setError(t("auth.changePassword.mismatch"));
      return;
    }
    if (newPassword.length < 8) {
      setError(t("auth.changePassword.tooShort"));
      return;
    }

    setIsSaving(true);
    try {
      await changePassword({ currentPassword, newPassword });
      if (!isMounted.current) return;
      setSuccess(true);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (submitError) {
      if (!isMounted.current) return;
      setError(formatChangePasswordError(submitError, t("auth.requestFailed"), t));
    } finally {
      if (isMounted.current) {
        setIsSaving(false);
      }
    }
  }

  return (
    <Dialog fullWidth maxWidth="sm" onClose={onClose} open>
      <form onSubmit={(event) => void handleSubmit(event)}>
        <DialogTitle>{t("auth.changePassword.title")}</DialogTitle>
        <DialogContent dividers>
          <Stack gap="md">
            {error ? <Alert severity="error">{error}</Alert> : null}
            {success ? <Alert severity="success">{t("auth.changePassword.success")}</Alert> : null}
            <TextInput
              autoComplete="current-password"
              disabled={isSaving || success}
              label={t("auth.changePassword.currentPassword")}
              required
              type="password"
              value={currentPassword}
              onChange={(event: ChangeEvent<HTMLInputElement>) => setCurrentPassword(event.currentTarget.value)}
            />
            <TextInput
              autoComplete="new-password"
              disabled={isSaving || success}
              label={t("auth.changePassword.newPassword")}
              required
              type="password"
              value={newPassword}
              onChange={(event: ChangeEvent<HTMLInputElement>) => setNewPassword(event.currentTarget.value)}
            />
            <TextInput
              autoComplete="new-password"
              disabled={isSaving || success}
              label={t("auth.changePassword.confirmPassword")}
              required
              type="password"
              value={confirmPassword}
              onChange={(event: ChangeEvent<HTMLInputElement>) => setConfirmPassword(event.currentTarget.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} type="button" variant="light">
            {t("legal.close")}
          </Button>
          {!success ? (
            <Button disabled={isSaving} loading={isSaving} type="submit" variant="filled">
              {t("auth.changePassword.submit")}
            </Button>
          ) : null}
        </DialogActions>
      </form>
    </Dialog>
  );
}
