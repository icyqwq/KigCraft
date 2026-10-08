import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Text, Title } from "../../ui/mui";

export type LegalDocument = "terms" | "privacy" | "cookies";

const documentKeys: LegalDocument[] = ["terms", "privacy", "cookies"];

export function LegalDocumentsDialog({
  document,
  onClose,
  open,
}: {
  document: LegalDocument;
  onClose: () => void;
  open: boolean;
}) {
  const { t } = useTranslation();
  const [selectedDocument, setSelectedDocument] = useState<LegalDocument>(document);

  useEffect(() => {
    if (open) setSelectedDocument(document);
  }, [document, open]);

  const prefix = `legal.documents.${selectedDocument}`;

  return (
    <Dialog fullWidth maxWidth="sm" onClose={onClose} open={open}>
      <DialogTitle>{t(`${prefix}.title`)}</DialogTitle>
      <DialogContent dividers>
        <Stack gap="md">
          <Stack direction="row" gap="xs" style={{ flexWrap: "wrap" }}>
            {documentKeys.map((item) => (
              <Button
                aria-pressed={selectedDocument === item}
                key={item}
                onClick={() => setSelectedDocument(item)}
                size="sm"
                variant={selectedDocument === item ? "filled" : "light"}
              >
                {t(`legal.documents.${item}.title`)}
              </Button>
            ))}
          </Stack>
          {(t(`${prefix}.sections`, { returnObjects: true }) as Array<{ heading: string; paragraphs: string[] }>).map(
            (section) => (
              <Stack gap="xs" key={section.heading}>
                <Title order={3} size="h6">{section.heading}</Title>
                {section.paragraphs.map((paragraph) => <Text key={paragraph}>{paragraph}</Text>)}
              </Stack>
            ),
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} variant="light">{t("legal.close")}</Button>
      </DialogActions>
    </Dialog>
  );
}
