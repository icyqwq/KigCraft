import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { updatePrivacyPreferences, type CurrentUser } from "../../api/client";
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Switch, Text } from "../../ui/mui";
import { LegalDocumentsDialog, type LegalDocument } from "./LegalDocumentsDialog";

export function PrivacySettingsDialog({
  authSessionEpoch,
  currentUser,
  onClose,
  onUpdated,
}: {
  authSessionEpoch: number;
  currentUser: CurrentUser;
  onClose: () => void;
  onUpdated: (user: CurrentUser, requestAuthSessionEpoch: number) => void;
}) {
  const { t } = useTranslation();
  const [consent, setConsent] = useState(currentUser.generation_quality_improvement_consent);
  const [isSaving, setIsSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [legalDocument, setLegalDocument] = useState<LegalDocument | null>(null);
  const isMounted = useRef(true);

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  useEffect(() => {
    setConsent(currentUser.generation_quality_improvement_consent);
  }, [currentUser.generation_quality_improvement_consent]);

  async function changeConsent(nextConsent: boolean) {
    const requestAuthSessionEpoch = authSessionEpoch;
    setIsSaving(true);
    setSaveFailed(false);
    try {
      const updatedUser = await updatePrivacyPreferences(nextConsent);
      onUpdated(updatedUser, requestAuthSessionEpoch);
      if (!isMounted.current) return;
      setConsent(updatedUser.generation_quality_improvement_consent);
    } catch {
      if (!isMounted.current) return;
      setSaveFailed(true);
    } finally {
      if (isMounted.current) {
        setIsSaving(false);
      }
    }
  }

  return (
    <>
      <Dialog fullWidth maxWidth="sm" onClose={onClose} open>
        <DialogTitle>{t("legal.privacySettings.title")}</DialogTitle>
        <DialogContent dividers>
          <Stack gap="md">
            {saveFailed ? <Alert severity="error">{t("legal.privacySettings.saveFailed")}</Alert> : null}
            <Stack gap="xs">
              <Switch
                aria-label={t("legal.privacySettings.qualityImprovement")}
                checked={consent}
                disabled={isSaving}
                label={t("legal.privacySettings.qualityImprovement")}
                onChange={(event) => void changeConsent(event.currentTarget.checked)}
              />
              <Text size="sm">{t("legal.privacySettings.description")}</Text>
            </Stack>
            <Stack direction="row" gap="xs" style={{ flexWrap: "wrap" }}>
              <Button onClick={() => setLegalDocument("privacy")} size="sm" variant="light">
                {t("legal.documents.privacy.title")}
              </Button>
              <Button onClick={() => setLegalDocument("cookies")} size="sm" variant="light">
                {t("legal.documents.cookies.title")}
              </Button>
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} variant="light">{t("legal.close")}</Button>
        </DialogActions>
      </Dialog>
      {legalDocument ? (
        <LegalDocumentsDialog document={legalDocument} onClose={() => setLegalDocument(null)} open />
      ) : null}
    </>
  );
}
