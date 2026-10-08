import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Paper, Stack, Text } from "../../ui/mui";

export const COOKIE_NOTICE_DISMISSED_STORAGE_KEY = "kig_cookie_notice_dismissed_v1";

function isDismissed() {
  try {
    return window.localStorage.getItem(COOKIE_NOTICE_DISMISSED_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function CookieNotice({ onViewCookieDocument }: { onViewCookieDocument: () => void }) {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(() => !isDismissed());

  if (!visible) return null;

  function dismiss() {
    try {
      window.localStorage.setItem(COOKIE_NOTICE_DISMISSED_STORAGE_KEY, "1");
    } catch {
      // The notice remains dismissible when storage is unavailable.
    }
    setVisible(false);
  }

  return (
    <Paper component="aside" p="md" role="region" withBorder>
      <Stack align="center" direction={{ xs: "column", sm: "row" }} gap="sm" justify="space-between">
        <Text>{t("legal.cookieNotice.body")}</Text>
        <Stack direction="row" gap="xs">
          <Button onClick={onViewCookieDocument} variant="light">{t("legal.cookieNotice.view")}</Button>
          <Button onClick={dismiss}>{t("legal.cookieNotice.dismiss")}</Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
