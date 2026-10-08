import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { resolveGenerationStatusLabel, resolveUserMessage } from "./resolveUserMessage";

export function useGenerationDisplay() {
  const { t } = useTranslation();

  const formatGenerationMessage = useCallback((value: unknown) => resolveUserMessage(value, t), [t]);
  const formatGenerationStatusLabel = useCallback(
    (status: string) => resolveGenerationStatusLabel(status, t),
    [t],
  );

  return {
    formatGenerationMessage,
    formatGenerationStatusLabel,
  };
}
