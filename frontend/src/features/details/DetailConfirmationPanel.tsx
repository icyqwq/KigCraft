import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  CircularProgress,
  Group,
  Image,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Textarea,
  Title,
  Tooltip,
} from "../../ui/mui";
import { IconArrowLeft, IconPlus, IconRefresh, IconSparkles, IconTrash, IconUpload, IconWand } from "@tabler/icons-react";
import type { TFunction } from "i18next";
import type { CSSProperties, ChangeEvent } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { DetailCrop, DetailFeature, DetailKind } from "../../api/client";
import { formatDuration, formatEtaDuration } from "../generation/generationTiming";

export type DetailConfirmationPanelProps = {
  analysisElapsedMs?: number | null;
  analysisEtaMs?: number | null;
  canGenerate: boolean;
  crops: DetailCrop[];
  features: DetailFeature[];
  isAnalyzing: boolean;
  warnings: string[];
  onBackToUpload: () => void;
  onAddCrop: (input: { description: string; file: File; kind: DetailKind }) => void | Promise<void>;
  onCropsChange: (crops: DetailCrop[]) => void;
  onFeaturesChange: (features: DetailFeature[]) => void;
  onGenerateFront: () => void | Promise<void>;
  onReanalyze: () => void | Promise<void>;
  onReplaceCrop: (cropId: string, file: File) => void | Promise<void>;
};

const ACCEPTED_IMAGE_TYPES = "image/png,image/jpeg,image/webp";
const MANUAL_DETAIL_KINDS: DetailKind[] = ["hair", "ears", "eyes", "expression", "headwear", "accessory"];
const visuallyHiddenStyle: CSSProperties = {
  clip: "rect(0 0 0 0)",
  clipPath: "inset(50%)",
  height: 1,
  overflow: "hidden",
  position: "absolute",
  whiteSpace: "nowrap",
  width: 1,
};

export function DetailConfirmationPanel({
  analysisElapsedMs = null,
  analysisEtaMs = null,
  canGenerate,
  crops,
  features,
  isAnalyzing,
  warnings,
  onBackToUpload,
  onAddCrop,
  onCropsChange,
  onFeaturesChange,
  onGenerateFront,
  onReanalyze,
  onReplaceCrop,
}: DetailConfirmationPanelProps) {
  const { i18n, t } = useTranslation();
  const [isAddingFeature, setIsAddingFeature] = useState(false);
  const [isAddingCrop, setIsAddingCrop] = useState(false);
  const [manualFeatureKind, setManualFeatureKind] = useState<DetailKind>("hair");
  const [manualFeatureDescription, setManualFeatureDescription] = useState("");
  const [manualCropKind, setManualCropKind] = useState<DetailKind>("hair");
  const [manualCropDescription, setManualCropDescription] = useState("");
  const [manualCropFile, setManualCropFile] = useState<File | null>(null);
  const shouldShowAnalysisStatus = isAnalyzing && analysisElapsedMs !== null;
  const visibleWarnings = warnings.filter(isVisibleDetailWarning);

  function updateFeatureDescription(featureId: string, description: string) {
    onFeaturesChange(features.map((feature) => (feature.id === featureId ? { ...feature, description } : feature)));
  }

  function deleteFeature(featureId: string) {
    onFeaturesChange(features.filter((feature) => feature.id !== featureId));
  }

  function updateCropDescription(cropId: string, description: string) {
    onCropsChange(crops.map((crop) => (crop.id === cropId ? { ...crop, description } : crop)));
  }

  function deleteCrop(cropId: string) {
    onCropsChange(crops.filter((crop) => crop.id !== cropId));
  }

  function replaceCrop(cropId: string, event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    if (!file) return;

    void onReplaceCrop(cropId, file);
    event.currentTarget.value = "";
  }

  function resetManualFeatureForm() {
    setIsAddingFeature(false);
    setManualFeatureKind("hair");
    setManualFeatureDescription("");
  }

  function addManualFeature() {
    const description = manualFeatureDescription.trim();
    if (!description) return;

    onFeaturesChange([
      ...features,
      {
        description,
        id: `manual-feature-${Date.now()}`,
        kind: manualFeatureKind,
        label: getDetailKindLabel(t, manualFeatureKind),
      },
    ]);
    resetManualFeatureForm();
  }

  function resetManualCropForm() {
    setIsAddingCrop(false);
    setManualCropKind("hair");
    setManualCropDescription("");
    setManualCropFile(null);
  }

  function addManualCrop() {
    const description = manualCropDescription.trim();
    if (!description || !manualCropFile) return;

    void onAddCrop({ description, file: manualCropFile, kind: manualCropKind });
    resetManualCropForm();
  }

  return (
    <Paper className="grunge-card" p={{ base: 2, md: 3 }} shadow="sm" withBorder>
      <Stack gap={2.5}>
        <Group align="flex-start" gap={2} justify="space-between" wrap="wrap">
          <Box>
            <Group align="center" gap={1}>
              <IconSparkles color="var(--kb-dirty-yellow)" size={21} />
              <Title order={2} size="h3">
                {t("detailConfirmation.title")}
              </Title>
            </Group>
            <Text c="dimmed" mt={0.75} size="sm">
              {t("detailConfirmation.subtitle")}
            </Text>
          </Box>

          <Group align="center" gap={1} justify="flex-end" wrap="wrap">
            <Button leftSection={<IconArrowLeft size={16} />} onClick={onBackToUpload} size="sm" variant="light">
              {t("detailConfirmation.backToUpload")}
            </Button>
            <Button
              disabled={isAnalyzing}
              leftSection={<IconRefresh size={16} />}
              loading={isAnalyzing}
              onClick={() => void onReanalyze()}
              size="sm"
              variant="light"
            >
              {t("detailConfirmation.reanalyze")}
            </Button>
            <Button
              color="cyan"
              disabled={!canGenerate || isAnalyzing}
              leftSection={<IconWand size={16} />}
              loading={isAnalyzing}
              onClick={() => void onGenerateFront()}
              size="sm"
              variant="filled"
            >
              {t("detailConfirmation.generateFront")}
            </Button>
          </Group>
        </Group>

        {shouldShowAnalysisStatus ? (
          <Paper
            p={{ base: 1.5, md: 2 }}
            withBorder
            style={{
              background: "var(--kb-panel)",
              borderColor: "var(--kb-line)",
            }}
          >
            <Group align="center" gap={1.5} justify="space-between" wrap="wrap">
              <Group align="center" gap={1.25} wrap="nowrap">
                <CircularProgress
                  aria-label={t("detailConfirmation.analysisStatus.title")}
                  color="cyan"
                  size={34}
                  thickness={4.5}
                />
                <Box>
                  <Text fw={800} size="sm">
                    {t("detailConfirmation.analysisStatus.title")}
                  </Text>
                  <Text c="dimmed" size="xs">
                    {t("detailConfirmation.analysisStatus.subtitle")}
                  </Text>
                </Box>
              </Group>
              <Group gap={0.75} justify="flex-end" wrap="wrap">
                <Badge color="gray" data-testid="detail-analysis-elapsed" radius="sm" variant="light">
                  {t("detailConfirmation.analysisStatus.elapsed", {
                    duration: formatDuration(analysisElapsedMs, i18n.language),
                  })}
                </Badge>
                {analysisEtaMs !== null ? (
                  <Badge color="cyan" data-testid="detail-analysis-eta" radius="sm" variant="light">
                    {t("detailConfirmation.analysisStatus.eta", {
                      duration: formatEtaDuration(analysisEtaMs, i18n.language),
                    })}
                  </Badge>
                ) : null}
              </Group>
            </Group>
          </Paper>
        ) : null}

        {visibleWarnings.length > 0 ? (
          <Stack gap={1}>
            {visibleWarnings.map((warning) => (
              <Alert key={warning} color="yellow" variant="light">
                {warning}
              </Alert>
            ))}
          </Stack>
        ) : null}

        <Stack gap={1.25}>
          <Group align="center" justify="space-between" wrap="wrap">
            <Title order={3} size="h4">
              {t("detailConfirmation.featureListTitle")}
            </Title>
            <Button
              leftSection={<IconPlus size={16} />}
              onClick={() => setIsAddingFeature(true)}
              size="sm"
              variant="light"
            >
              {t("detailConfirmation.addFeature")}
            </Button>
          </Group>
          {isAddingFeature ? (
            <ManualDetailForm
              description={manualFeatureDescription}
              descriptionLabel={t("detailConfirmation.manualDescriptionLabel")}
              formId="manual-feature"
              kind={manualFeatureKind}
              kindLabel={t("detailConfirmation.manualKindLabel")}
              onCancel={resetManualFeatureForm}
              onDescriptionChange={setManualFeatureDescription}
              onKindChange={setManualFeatureKind}
              onSave={addManualFeature}
              saveLabel={t("detailConfirmation.saveManualFeature")}
              t={t}
            />
          ) : null}
          {features.length > 0 ? (
            <Stack gap={1}>
              {features.map((feature) => {
                const label = getDetailKindLabel(t, feature.kind);
                const inputId = `detail-feature-${feature.id}`;

                return (
                  <Group key={feature.id} align="flex-start" gap={1.25} wrap="nowrap">
                    <Badge variant="light">{label}</Badge>
                    <Box component="label" htmlFor={inputId} style={visuallyHiddenStyle}>
                      {t("detailConfirmation.featureInputAria", { kind: label })}
                    </Box>
                    <Textarea
                      id={inputId}
                      maxRows={3}
                      minRows={1}
                      onChange={(event: ChangeEvent<HTMLInputElement>) =>
                        updateFeatureDescription(feature.id, event.currentTarget.value)
                      }
                      placeholder={t("detailConfirmation.featurePlaceholder")}
                      value={feature.description}
                    />
                    <Tooltip label={t("detailConfirmation.deleteFeature", { description: feature.description })}>
                      <ActionIcon
                        aria-label={t("detailConfirmation.deleteFeatureAria", {
                          description: feature.description,
                        })}
                        color="red"
                        onClick={() => deleteFeature(feature.id)}
                        size="sm"
                        variant="light"
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                );
              })}
            </Stack>
          ) : (
            <Text c="dimmed" size="sm">
              {t("detailConfirmation.noFeatures")}
            </Text>
          )}
        </Stack>

        <Stack gap={1.25}>
          <Group align="center" justify="space-between" wrap="wrap">
            <Title order={3} size="h4">
              {t("detailConfirmation.cropListTitle")}
            </Title>
            <Button
              leftSection={<IconPlus size={16} />}
              onClick={() => setIsAddingCrop(true)}
              size="sm"
              variant="light"
            >
              {t("detailConfirmation.addCrop")}
            </Button>
          </Group>
          {isAddingCrop ? (
            <ManualDetailForm
              description={manualCropDescription}
              descriptionLabel={t("detailConfirmation.manualDescriptionLabel")}
              file={manualCropFile}
              formId="manual-crop"
              imageLabel={t("detailConfirmation.manualImageLabel")}
              kind={manualCropKind}
              kindLabel={t("detailConfirmation.manualKindLabel")}
              onCancel={resetManualCropForm}
              onDescriptionChange={setManualCropDescription}
              onFileChange={setManualCropFile}
              onKindChange={setManualCropKind}
              onSave={addManualCrop}
              saveDisabled={!manualCropFile}
              saveLabel={t("detailConfirmation.saveManualCrop")}
              t={t}
            />
          ) : null}
          {crops.length > 0 ? (
            <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }} spacing={1.5}>
              {crops.map((crop) => {
                const label = getDetailKindLabel(t, crop.kind);
                const inputId = `detail-crop-${crop.id}`;
                const deleteCropLabel = t("detailConfirmation.deleteCrop", { description: crop.description });
                const deleteCropAriaLabel = t("detailConfirmation.deleteCropAria", {
                  description: crop.description,
                });
                const replaceCropAriaLabel = t("detailConfirmation.replaceCropAria", {
                  description: crop.description,
                });

                return (
                  <Paper
                    key={crop.id}
                    p={1.5}
                    withBorder
                    style={{
                      background: "var(--kb-panel)",
                      borderColor: "var(--kb-line)",
                      overflow: "hidden",
                    }}
                  >
                    <Stack gap={1.25}>
                      <Box
                        style={{
                          alignItems: "center",
                          aspectRatio: "4 / 3",
                          background: "var(--kb-old-paper-2)",
                          border: "2px solid var(--kb-line)",
                          display: "flex",
                          overflow: "hidden",
                        }}
                      >
                        <Image alt={crop.description} fit="cover" h="100%" src={crop.imageUrl} w="100%" />
                      </Box>

                      <Group align="center" gap={1} justify="space-between" wrap="nowrap">
                        <Badge variant="light">{label}</Badge>
                        <Tooltip label={deleteCropLabel}>
                          <ActionIcon
                            aria-label={deleteCropAriaLabel}
                            color="red"
                            onClick={() => deleteCrop(crop.id)}
                            size="sm"
                            variant="light"
                          >
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>

                      <Textarea
                        id={inputId}
                        maxRows={3}
                        minRows={2}
                        onChange={(event: ChangeEvent<HTMLInputElement>) =>
                          updateCropDescription(crop.id, event.currentTarget.value)
                        }
                        placeholder={t("detailConfirmation.cropPlaceholder")}
                        value={crop.description}
                      />
                      <Box component="label" htmlFor={inputId} style={visuallyHiddenStyle}>
                        {t("detailConfirmation.cropInputAria", { kind: label })}
                      </Box>

                      <Button component="label" leftSection={<IconUpload size={16} />} size="sm" variant="light">
                        {t("common.replace")}
                        <input
                          accept={ACCEPTED_IMAGE_TYPES}
                          aria-label={replaceCropAriaLabel}
                          onChange={(event) => replaceCrop(crop.id, event)}
                          style={{
                            clip: "rect(0 0 0 0)",
                            clipPath: "inset(50%)",
                            height: 1,
                            overflow: "hidden",
                            position: "absolute",
                            whiteSpace: "nowrap",
                            width: 1,
                          }}
                          type="file"
                        />
                      </Button>
                    </Stack>
                  </Paper>
                );
              })}
            </SimpleGrid>
          ) : (
            <Text c="dimmed" size="sm">
              {t("detailConfirmation.noCrops")}
            </Text>
          )}
        </Stack>
      </Stack>
    </Paper>
  );
}

function ManualDetailForm({
  description,
  descriptionLabel,
  file = null,
  formId,
  imageLabel,
  kind,
  kindLabel,
  onCancel,
  onDescriptionChange,
  onFileChange,
  onKindChange,
  onSave,
  saveDisabled = false,
  saveLabel,
  t,
}: {
  description: string;
  descriptionLabel: string;
  file?: File | null;
  formId: string;
  imageLabel?: string;
  kind: DetailKind;
  kindLabel: string;
  onCancel: () => void;
  onDescriptionChange: (description: string) => void;
  onFileChange?: (file: File | null) => void;
  onKindChange: (kind: DetailKind) => void;
  onSave: () => void;
  saveDisabled?: boolean;
  saveLabel: string;
  t: TFunction;
}) {
  return (
    <Paper
      p={1.5}
      withBorder
      style={{
        background: "var(--kb-panel)",
        borderColor: "var(--kb-line)",
      }}
    >
      <Stack gap={1.25}>
        <Group align="flex-start" gap={1.25} wrap="wrap">
          <Box style={{ minWidth: 150 }}>
            <Text component="label" fw={800} size="xs" htmlFor={`${formId}-kind`}>
              {kindLabel}
            </Text>
            <Box
              component="select"
              id={`${formId}-kind`}
              onChange={(event: ChangeEvent<HTMLSelectElement>) => onKindChange(event.currentTarget.value as DetailKind)}
              value={kind}
              style={{
                background: "var(--kb-old-paper-2)",
                border: "2px solid var(--kb-line)",
                borderRadius: 6,
                color: "inherit",
                display: "block",
                font: "inherit",
                fontWeight: 700,
                marginTop: 6,
                padding: "9px 10px",
                width: "100%",
              }}
            >
              {MANUAL_DETAIL_KINDS.map((item) => (
                <option key={item} value={item}>
                  {getDetailKindLabel(t, item)}
                </option>
              ))}
            </Box>
          </Box>
          <Box style={{ flex: "1 1 260px" }}>
            <Textarea
              label={descriptionLabel}
              maxRows={3}
              minRows={1}
              onChange={(event: ChangeEvent<HTMLInputElement>) => onDescriptionChange(event.currentTarget.value)}
              value={description}
            />
          </Box>
        </Group>

        {onFileChange && imageLabel ? (
          <Button component="label" leftSection={<IconUpload size={16} />} size="sm" variant="light">
            {file ? file.name : imageLabel}
            <input
              accept={ACCEPTED_IMAGE_TYPES}
              aria-label={imageLabel}
              onChange={(event) => {
                onFileChange(event.currentTarget.files?.[0] ?? null);
                event.currentTarget.value = "";
              }}
              style={visuallyHiddenStyle}
              type="file"
            />
          </Button>
        ) : null}

        <Group gap={1} justify="flex-end" wrap="wrap">
          <Button onClick={onCancel} size="sm" variant="light">
            {t("detailConfirmation.cancelManual")}
          </Button>
          <Button
            color="cyan"
            disabled={!description.trim() || saveDisabled}
            onClick={onSave}
            size="sm"
            variant="filled"
          >
            {saveLabel}
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}

function getDetailKindLabel(t: TFunction, kind: DetailKind) {
  return t(`detailConfirmation.kind.${kind}`);
}

function isVisibleDetailWarning(warning: string) {
  const normalized = warning.trim().toLowerCase();
  if (!normalized) return false;
  if (normalized.includes("skipped non-head detail")) return false;
  if (warning.includes("未记录") || warning.includes("未列出")) return false;
  return true;
}
