import {
  Alert,
  ActionIcon,
  Badge,
  Box,
  Button,
  Container,
  Group,
  Menu,
  MenuItem,
  Paper,
  Stack,
  Tab,
  Tabs,
  Text,
  ThemeIcon,
  Title,
  useMediaQuery,
} from "../ui/mui";
import {
  IconAlertCircle,
  IconBrandGithub,
  IconBrandQq,
  IconBrush,
  IconHistory,
  IconInfoCircle,
  IconLanguage,
  IconMenu2,
  IconSparkles,
  IconUpload,
  IconWand,
} from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  ApiError,
  analyzeReferenceDetails,
  createGenerationJob,
  getGenerationEvents,
  getGenerationJob,
  uploadReferenceFile,
  type DetailAnalysis,
  type DetailCrop,
  type DetailFeature,
  type DetailKind,
  type DetailLockInput,
  type GenerationJob,
  type GenerationMode,
  type JobEvent,
} from "../api/client";
import { DetailConfirmationPanel } from "../features/details/DetailConfirmationPanel";
import {
  EditorWorkspace,
  type EditorImageSavePayload,
  type EditorRegeneratePayload,
  type EditorWorkspaceHandle,
} from "../features/editor/EditorWorkspace";
import { completeLandmarks, type ManualLandmarks } from "../features/editor/deformation/landmarks";
import type { EditRecipe } from "../features/editor/deformation/recipe";
import { GenerationReview } from "../features/generation/GenerationReview";
import { formatGenerationMessage } from "../features/generation/generationDisplay";
import {
  appendGenerationDurationSample,
  estimateGenerationDurationMs,
  readGenerationDurationHistory,
  writeGenerationDurationHistory,
} from "../features/generation/generationTiming";
import { LanguageSelector } from "../features/i18n/LanguageSelector";
import { PromptComposer } from "../features/prompt-composer/PromptComposer";
import { ReferenceUploader } from "../features/upload/ReferenceUploader";
import {
  buildInitialWorkflowState,
  restoreWorkflowState,
  serializeWorkflowState,
} from "../features/workflow/useWorkflowState";
import type { ReferenceKind, ReferenceSlot, WorkflowStep } from "../features/workflow/workflowTypes";
import { normalizeLocale, SUPPORTED_LOCALES, writeStoredLocale, type AppLocale } from "../i18n/locales";

const WORKFLOW_STORAGE_KEY = "kig-preview.workflow.v2";

type HeaderView = "workbench" | "editor" | "history" | "about";

type EditorImage = {
  fileName?: string;
  height?: number;
  id: string;
  imageUrl: string;
  index: number;
  jobId?: string;
  landmarks?: ManualLandmarks | null;
  width?: number;
};

type DirectRefineImage = {
  fileName: string;
  id: string;
  imageUrl: string;
};

const headerNavItems = [
  { id: "workbench", labelKey: "workflow.nav.workbench", icon: IconSparkles },
  { id: "editor", labelKey: "workflow.nav.editor", icon: IconBrush },
  { id: "history", labelKey: "workflow.nav.history", icon: IconHistory },
  { id: "about", labelKey: "workflow.nav.about", icon: IconInfoCircle },
] as const satisfies Array<{ id: HeaderView; labelKey: string; icon: typeof IconSparkles }>;

const openSourceProjects = [
  { name: "React", purpose: "前端界面运行时", url: "https://react.dev/", version: "19.1.0" },
  { name: "Vite", purpose: "前端构建与开发服务", url: "https://vite.dev/", version: "7.0.0" },
  { name: "Material UI", purpose: "基础 UI 组件", url: "https://mui.com/material-ui/", version: "9.1.2" },
  { name: "Tabler Icons", purpose: "界面图标", url: "https://tabler.io/icons", version: "3.44.0" },
  {
    name: "TanStack Query",
    purpose: "服务端状态与轮询",
    url: "https://tanstack.com/query/latest",
    version: "5.81.5",
  },
  { name: "PixiJS", purpose: "图像编辑画布渲染", url: "https://pixijs.com/", version: "8.10.1" },
  { name: "Konva", purpose: "标注图形交互", url: "https://konvajs.org/", version: "10.3.0" },
  { name: "ONNX Runtime Web", purpose: "浏览器端模型推理", url: "https://onnxruntime.ai/", version: "1.27.0" },
  {
    name: "MediaPipe Tasks Vision",
    purpose: "图像视觉能力支持",
    url: "https://ai.google.dev/edge/mediapipe/solutions/vision",
    version: "0.10.35",
  },
  { name: "Zod", purpose: "数据结构校验", url: "https://zod.dev/", version: "3.25.67" },
] as const;

const workflowSteps = [
  { labelKey: "workflow.steps.upload", icon: IconUpload },
  { labelKey: "workflow.steps.detail", icon: IconSparkles },
  { labelKey: "workflow.steps.front", icon: IconWand },
  { labelKey: "workflow.steps.turnaround", icon: IconBrush },
];

const terminalGenerationStatuses = new Set(["succeeded", "failed", "cancelled", "accepted"]);

export function isTerminalGenerationStatus(status?: string) {
  return status ? terminalGenerationStatuses.has(status) : false;
}

function hasExpectedGenerationOutputs(job: GenerationJob | null | undefined) {
  if (!job || job.outputs.length === 0) return false;
  return job.outputs.length >= Math.max(1, job.expected_output_count || 1);
}

function isGenerationReadyForEditor(job: GenerationJob | null | undefined) {
  if (!job || job.outputs.length === 0) return false;
  return isTerminalGenerationStatus(job.status) || hasExpectedGenerationOutputs(job) || job.progress >= 100;
}

function shouldPollGenerationJob(job: GenerationJob | null | undefined) {
  return Boolean(job && !isTerminalGenerationStatus(job.status));
}

function isGenerationJobForEditorMode(job: GenerationJob | null | undefined, mode: "front" | "turnaround") {
  if (!job) return false;
  return mode === "turnaround" ? job.generation_mode === "turnaround" : job.generation_mode !== "turnaround";
}

function readPersistedWorkflowState() {
  if (typeof window === "undefined") return buildInitialWorkflowState();
  return restoreWorkflowState(window.localStorage.getItem(WORKFLOW_STORAGE_KEY));
}

function workflowStepToIndex(step: WorkflowStep) {
  return Math.max(0, Math.min(workflowSteps.length - 1, step - 1));
}

function indexToWorkflowStep(index: number): WorkflowStep {
  return (Math.max(0, Math.min(workflowSteps.length - 1, index)) + 1) as WorkflowStep;
}

function referenceSlotToKey(slot: ReferenceSlot) {
  const objectKey = slot.objectKey?.trim();
  return objectKey ? `${slot.kind}:${objectKey}` : null;
}

function getReferenceKeys(referenceSlots: ReferenceSlot[]) {
  return referenceSlots.flatMap((slot) => {
    const key = referenceSlotToKey(slot);
    return key ? [key] : [];
  });
}

function getReferenceDescriptions(referenceSlots: ReferenceSlot[]) {
  return referenceSlots.flatMap((slot) => {
    const key = referenceSlotToKey(slot);
    const description = slot.description?.trim();
    return key && description ? [{ description, referenceKey: key }] : [];
  });
}

function getDetailReferenceKeys(crops: DetailCrop[]) {
  return crops.map((crop) => `detail:${crop.objectKey}`);
}

function getDetailReferenceDescriptions(crops: DetailCrop[]) {
  return crops.flatMap((crop) => {
    const description = crop.description.trim();
    return description ? [{ description, referenceKey: `detail:${crop.objectKey}` }] : [];
  });
}

function hasReferenceSlot(referenceSlots: ReferenceSlot[], kind: ReferenceKind) {
  return referenceSlots.some((slot) => slot.kind === kind && Boolean(slot.objectKey?.trim() || slot.file));
}

async function uploadPendingReferenceSlots(referenceSlots: ReferenceSlot[]) {
  let didUpload = false;
  const uploadedSlots: ReferenceSlot[] = [];

  for (const slot of referenceSlots) {
    if (!slot.file) {
      uploadedSlots.push(slot);
      continue;
    }

    const upload = await uploadReferenceFile(slot.kind, slot.file);
    didUpload = true;
    uploadedSlots.push({
      ...slot,
      file: undefined,
      fileName: upload.fileName,
      objectKey: upload.objectKey,
    });
  }

  return didUpload ? uploadedSlots : referenceSlots;
}

function getEditorRecipeKey(image: EditorImage) {
  return `front:${image.jobId ?? image.id}:${image.index}`;
}

function getDirectRefineRecipeKey(image: DirectRefineImage) {
  return `direct:${image.id}`;
}

function directRefineToEditorImage(image: DirectRefineImage): EditorImage {
  return {
    fileName: image.fileName,
    id: image.id,
    imageUrl: image.imageUrl,
    index: 1,
  };
}

function outputToEditorImage(job: GenerationJob, index: number): EditorImage | null {
  const output = job.outputs.find((candidate) => candidate.index === index) ?? job.outputs[0];
  if (!output) return null;
  return {
    height: output.height,
    id: `front:${job.id}:${output.index}`,
    imageUrl: output.image_url,
    index: output.index,
    jobId: job.id,
    landmarks: output.landmarks ? completeLandmarks(output.landmarks) : null,
    width: output.width,
  };
}

function areEditorImagesEquivalent(left: EditorImage | null, right: EditorImage) {
  return Boolean(
    left &&
      left.id === right.id &&
      left.imageUrl === right.imageUrl &&
      left.width === right.width &&
      left.height === right.height &&
      JSON.stringify(left.landmarks ?? null) === JSON.stringify(right.landmarks ?? null),
  );
}

export function createEditorReferenceSlots(payload: EditorRegeneratePayload, labelPrefix: string): ReferenceSlot[] {
  const frontImageBlob = payload.annotatedImageBlob ?? payload.editedImageBlob;
  const slots: ReferenceSlot[] = [
    {
      file: new File([frontImageBlob], `${labelPrefix}-edited.png`, {
        type: frontImageBlob.type || "image/png",
      }),
      kind: "front",
      label: "已编辑正视图",
      required: true,
    },
  ];

  if (payload.annotatedImageBlob) {
    slots.push({
      file: new File([payload.annotatedImageBlob], `${labelPrefix}-annotations.png`, {
        type: payload.annotatedImageBlob.type || "image/png",
      }),
      kind: "annotation",
      label: "编辑标注图",
      required: false,
    });
  }

  if (payload.extraReference) {
    slots.push({
      file: payload.extraReference.file,
      kind: "supplemental",
      label: "补充参考图",
      required: false,
      description:
        payload.extraReference.description ||
        "用户上传的补充参考图，用于重新生成时对齐表情、发饰、配件或局部风格。",
    });
  }

  return slots;
}

function buildEditorExtraPrompt(payload: EditorRegeneratePayload) {
  return [payload.annotationPrompt, payload.promptNote?.trim()].filter(Boolean).join("\n\n");
}

function formatProviderFailureDetail(detail: string) {
  const providerFailure = detail.match(/^([a-z_]+)_provider_failed:\s*(.+)$/i);
  if (providerFailure) {
    const operation = providerFailure[1].replace(/_/g, " ");
    return `生成服务 ${operation} 失败：${providerFailure[2]}`;
  }
  const providerConfig = detail.match(
    /^(real_generation_provider_not_configured|fixture_generation_disabled_in_production):\s*(.+)$/i,
  );
  if (providerConfig) {
    return `真实生成服务未配置：${providerConfig[2]}`;
  }
  return null;
}

function formatGenerationError(error: unknown) {
  if (error instanceof ApiError) {
    if (typeof error.detail === "string") {
      if (
        error.detail === "detail_analysis_request_timeout" ||
        /Codex CLI timed out/i.test(error.detail)
      ) {
        return "细节分析超时，生成服务暂时没有返回结果。请稍后重试，或换一张更清晰、更小的参考图。";
      }
      if (error.detail === "reference_adult_explicit") {
        return "图片内容不适合用于生成，请更换参考图。";
      }
      if (error.detail === "reference_unusable") {
        return "未识别到可用的角色头部参考，请更换图片。";
      }
      const providerDetail = formatProviderFailureDetail(error.detail);
      if (providerDetail) return providerDetail;
      return formatGenerationMessage(error.detail);
    }
    return `生成请求失败 (${error.status})`;
  }
  if (error instanceof Error) {
    if (/load failed|failed to fetch|network/i.test(error.message)) {
      return "上传失败，可能是图片过大或网络中断。请换一张较小图片或稍后重试。";
    }
    return error.message;
  }
  return "生成请求失败";
}

function GenerationControls({
  canGenerate,
  isGenerating,
  label,
  onGenerate,
}: {
  canGenerate: boolean;
  isGenerating: boolean;
  label: string;
  onGenerate: () => void | Promise<void>;
}) {
  return (
    <Group align="center" justify="flex-end" gap={1.5}>
      <Button disabled={!canGenerate} loading={isGenerating} onClick={() => void onGenerate()}>
        {isGenerating ? "生成中" : label}
      </Button>
    </Group>
  );
}

function DirectRefineUploader({ onImageSelected }: { onImageSelected: (file: File) => void }) {
  return (
    <Paper p={{ base: 2, md: 3 }} withBorder>
      <Stack gap={2}>
        <Group align="center" gap={1.5} wrap="nowrap">
          <ThemeIcon color="cyan" radius={2} size={40} variant="light">
            <IconBrush size={22} />
          </ThemeIcon>
          <Box>
            <Title order={3} size="h4">
              本地图片编辑器
            </Title>
            <Text c="dimmed" mt={0.5} size="sm">
              标注、脸型、眼睛、液化。
            </Text>
          </Box>
        </Group>

        <Button component="label" leftSection={<IconUpload size={16} />} variant="light">
          选择图片
          <input
            accept="image/png,image/jpeg,image/webp"
            aria-label="选择本地编辑图片"
            data-testid="direct-refine-file-input"
            hidden
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              if (file) onImageSelected(file);
              event.currentTarget.value = "";
            }}
            type="file"
          />
        </Button>
      </Stack>
    </Paper>
  );
}

function TaskHistoryPanel({
  events,
  latestJob,
}: {
  events: JobEvent[];
  latestJob: GenerationJob | null | undefined;
}) {
  return (
    <Stack gap={3}>
      <Box>
        <Title order={1} size="h2">
          任务记录
        </Title>
        <Text c="dimmed" mt={0.75}>
          生成状态。
        </Text>
      </Box>

      {latestJob ? (
        <Paper p={3} withBorder>
          <Stack gap={2}>
            <Group justify="space-between" wrap="nowrap">
              <Box>
                <Text fw={800}>任务 {latestJob.id.slice(0, 8)}</Text>
                <Text c="dimmed" size="sm">
                  {formatGenerationMessage(latestJob.phase_label || latestJob.status)}
                </Text>
              </Box>
              <Badge color={isTerminalGenerationStatus(latestJob.status) ? "green" : "cyan"} variant="light">
                {formatGenerationMessage(latestJob.status)}
              </Badge>
            </Group>
            <Stack gap={1}>
              {events.slice(-5).map((event) => (
                <Group key={`${event.sequence}-${event.type}`} justify="space-between" wrap="nowrap">
                  <Text size="sm">{formatGenerationMessage(event.message || event.type)}</Text>
                </Group>
              ))}
            </Stack>
          </Stack>
        </Paper>
      ) : (
        <Paper p={3} withBorder>
          <Text c="dimmed">暂无任务。</Text>
        </Paper>
      )}
    </Stack>
  );
}

function formatBuildDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    hour12: false,
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(date)} UTC+8`;
}

function AboutPanel() {
  return (
    <Stack gap={2}>
      <Paper className="grunge-card" p={{ base: 2, md: 3 }} withBorder>
        <Stack gap={2}>
          <Box>
            <Title order={1} size="h2">
              关于 KigCraft
            </Title>
            <Text c="dimmed" mt={0.75}>
              KigCraft 是用于 Kigurumi 头壳预览图生成、编辑和标注的工具。
            </Text>
          </Box>

          <Group align="center" gap={1} wrap="wrap">
            <Badge color="cyan" variant="light">
              最后构建
            </Badge>
            <Text fw={900}>{formatBuildDate(__KIGCRAFT_BUILD_DATE__)}</Text>
          </Group>
        </Stack>
      </Paper>

      <Paper className="grunge-card" p={{ base: 2, md: 3 }} withBorder>
        <Stack gap={2}>
          <Box>
            <Title order={2} size="h3">
              开源项目声明
            </Title>
            <Text c="dimmed" mt={0.75} size="sm">
              本站使用以下主要开源项目构建。各项目版权归原作者所有，许可证以对应项目为准。
            </Text>
          </Box>

          <Box
            style={{
              display: "grid",
              gap: 12,
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            }}
          >
            {openSourceProjects.map((project) => (
              <Paper
                key={project.name}
                p={2}
                style={{
                  background: "var(--kb-panel-soft)",
                  boxShadow: "var(--kb-hard-shadow-sm)",
                }}
                withBorder
              >
                <Stack gap={1}>
                  <Group align="flex-start" justify="space-between" wrap="nowrap">
                    <Box>
                      <Text
                        component="a"
                        fw={900}
                        href={project.url}
                        rel="noreferrer"
                        style={{ color: "inherit", textDecoration: "none" }}
                        target="_blank"
                      >
                        {project.name}
                      </Text>
                      <Text c="dimmed" size="sm">
                        {project.purpose}
                      </Text>
                    </Box>
                    <Badge color="gray" variant="light">
                      {project.version}
                    </Badge>
                  </Group>
                </Stack>
              </Paper>
            ))}
          </Box>
        </Stack>
      </Paper>
    </Stack>
  );
}

export function WorkflowPage() {
  const { i18n, t } = useTranslation();
  const currentLocale = normalizeLocale(i18n.language);
  const persistedWorkflow = readPersistedWorkflowState();
  const isMobileHeader = useMediaQuery("(max-width: 960px)");
  const [activeHeaderView, setActiveHeaderView] = useState<HeaderView>("workbench");
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [activeStep, setActiveStep] = useState(() => workflowStepToIndex(persistedWorkflow.step));
  const [referenceSlots, setReferenceSlots] = useState<ReferenceSlot[]>(persistedWorkflow.referenceSlots);
  const [selectedRequirementIds, setSelectedRequirementIds] = useState<string[]>(persistedWorkflow.selectedRequirementIds);
  const [freeText, setFreeText] = useState(persistedWorkflow.freeText);
  const [characterSessionId, setCharacterSessionId] = useState<string | null>(persistedWorkflow.characterSessionId);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(persistedWorkflow.activeJobId);
  const [frontImage, setFrontImage] = useState<EditorImage | null>(null);
  const [turnaroundImage, setTurnaroundImage] = useState<EditorImage | null>(null);
  const [directRefineImage, setDirectRefineImage] = useState<DirectRefineImage | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [submittingGenerationMode, setSubmittingGenerationMode] = useState<GenerationMode | null>(null);
  const [generationError, setGenerationError] = useState<string | null>(null);
  const [detailAnalysisId, setDetailAnalysisId] = useState<string | null>(
    persistedWorkflow.detailConfirmation.analysisId,
  );
  const [detailFeatures, setDetailFeatures] = useState<DetailFeature[]>(
    persistedWorkflow.detailConfirmation.features,
  );
  const [detailCrops, setDetailCrops] = useState<DetailCrop[]>(persistedWorkflow.detailConfirmation.crops);
  const [detailWarnings, setDetailWarnings] = useState<string[]>(persistedWorkflow.detailConfirmation.warnings);
  const [isAnalyzingDetails, setIsAnalyzingDetails] = useState(false);
  const [detailAnalysisStartedAtMs, setDetailAnalysisStartedAtMs] = useState<number | null>(null);
  const [detailAnalysisNowMs, setDetailAnalysisNowMs] = useState(() => Date.now());
  const [detailAnalysisDurationHistory, setDetailAnalysisDurationHistory] = useState(readGenerationDurationHistory);
  const [editorRecipes, setEditorRecipes] = useState<Record<string, EditRecipe>>({});
  const [eventsUnavailableJobIds, setEventsUnavailableJobIds] = useState<Set<string>>(() => new Set());

  async function selectHeaderLocale(locale: AppLocale) {
    writeStoredLocale(locale);
    await i18n.changeLanguage(locale);
    setMenuAnchor(null);
  }
  const generateRequestId = useRef(0);
  const detailInputVersion = useRef(0);
  const cropReplacementRequestIds = useRef<Record<string, number>>({});
  const frontEditorRef = useRef<EditorWorkspaceHandle | null>(null);
  const turnaroundEditorRef = useRef<EditorWorkspaceHandle | null>(null);

  const jobQuery = useQuery({
    queryKey: ["generation-job", activeJobId],
    queryFn: () => getGenerationJob(activeJobId as string),
    enabled: Boolean(activeJobId),
    retry: (failureCount, error) => !(error instanceof ApiError && error.status === 404) && failureCount < 2,
    refetchInterval: (query) => (shouldPollGenerationJob(query.state.data) ? 2000 : false),
  });
  const latestJob = jobQuery.data ?? job;
  const eventsJobId = latestJob?.id ?? null;
  const shouldLoadGenerationEvents = Boolean(eventsJobId && !eventsUnavailableJobIds.has(eventsJobId));
  const eventsQuery = useQuery({
    queryKey: ["generation-events", eventsJobId],
    queryFn: () => getGenerationEvents(eventsJobId as string),
    enabled: shouldLoadGenerationEvents,
    retry: (failureCount, error) => !(error instanceof ApiError && error.status === 404) && failureCount < 2,
    refetchInterval: shouldPollGenerationJob(latestJob) ? 2000 : false,
  });
  const generationEvents = eventsQuery.data ?? [];
  const isGenerationActive = isGenerating || Boolean(latestJob && !isTerminalGenerationStatus(latestJob.status));
  const isDetailConfirmationBusy = isAnalyzingDetails || isGenerationActive;
  const detailAnalysisEstimateMs = estimateGenerationDurationMs(detailAnalysisDurationHistory, {
    generationMode: "detail_analysis",
  });
  const detailAnalysisElapsedMs =
    isAnalyzingDetails && detailAnalysisStartedAtMs !== null
      ? Math.max(0, detailAnalysisNowMs - detailAnalysisStartedAtMs)
      : null;
  const detailAnalysisEtaMs =
    detailAnalysisElapsedMs !== null ? Math.max(0, detailAnalysisEstimateMs - detailAnalysisElapsedMs) : null;
  const hasRequiredFrontReference = hasReferenceSlot(referenceSlots, "front");
  const canAnalyzeDetails = hasRequiredFrontReference && !isGenerationActive && !isAnalyzingDetails;
  function canSubmitConfirmedDetails() {
    return (
      hasRequiredFrontReference &&
      !isGenerationActive &&
      !isAnalyzingDetails &&
      (detailFeatures.some((feature) => Boolean(feature.description.trim())) || detailCrops.length > 0)
    );
  }
  const frontRecipeKey = frontImage ? getEditorRecipeKey(frontImage) : null;
  const turnaroundRecipeKey = turnaroundImage
    ? getEditorRecipeKey(turnaroundImage)
    : null;

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(
      WORKFLOW_STORAGE_KEY,
      serializeWorkflowState({
        activeJobId,
        characterSessionId,
        freeText,
        referenceSlots,
        detailConfirmation: {
          analysisId: detailAnalysisId,
          crops: detailCrops,
          features: detailFeatures,
          warnings: detailWarnings,
        },
        selectedCandidateIndex: frontImage?.index ?? null,
        selectedRequirementIds,
        step: indexToWorkflowStep(activeStep),
      }),
    );
  }, [
    activeJobId,
    activeStep,
    characterSessionId,
    detailAnalysisId,
    detailCrops,
    detailFeatures,
    detailWarnings,
    freeText,
    frontImage?.index,
    referenceSlots,
    selectedRequirementIds,
  ]);

  useEffect(() => {
    if (!jobQuery.data) return;
    setJob(jobQuery.data);
    if (isGenerationReadyForEditor(jobQuery.data)) setIsGenerating(false);
  }, [jobQuery.data]);

  useEffect(() => {
    const queryError = jobQuery.error;
    if (!(queryError instanceof ApiError) || queryError.status !== 404) return;

    setActiveJobId(null);
    setJob(null);
    setIsGenerating(false);
    setSubmittingGenerationMode(null);
    setGenerationError("当前生成任务已失效，请重新生成。");
  }, [jobQuery.error]);

  useEffect(() => {
    const queryError = eventsQuery.error;
    if (!(queryError instanceof ApiError) || queryError.status !== 404 || !eventsJobId) return;

    setEventsUnavailableJobIds((current) => {
      if (current.has(eventsJobId)) return current;
      const next = new Set(current);
      next.add(eventsJobId);
      return next;
    });
  }, [eventsJobId, eventsQuery.error]);

  useEffect(() => {
    if (!latestJob || !isGenerationReadyForEditor(latestJob)) return;
    const nextImage = outputToEditorImage(latestJob, 1);
    if (!nextImage) return;
    if (latestJob.generation_mode === "turnaround") {
      setTurnaroundImage((current) => (areEditorImagesEquivalent(current, nextImage) ? current : nextImage));
      setDirectRefineImage(null);
      setActiveStep(3);
      return;
    }
    setFrontImage((current) => (areEditorImagesEquivalent(current, nextImage) ? current : nextImage));
    setTurnaroundImage(null);
    setDirectRefineImage(null);
    setActiveStep(2);
  }, [latestJob]);

  useEffect(() => {
    if (!isAnalyzingDetails || detailAnalysisStartedAtMs === null || typeof window === "undefined") return;

    const updateNow = () => setDetailAnalysisNowMs(Date.now());
    updateNow();
    const intervalId = window.setInterval(updateNow, 1000);
    return () => window.clearInterval(intervalId);
  }, [detailAnalysisStartedAtMs, isAnalyzingDetails]);

  function canOpenStep(stepIndex: number) {
    return stepIndex >= 0 && stepIndex < workflowSteps.length;
  }

  function openStep(stepIndex: number) {
    if (!canOpenStep(stepIndex)) return;
    setActiveStep(stepIndex);
  }

  function clearDetailConfirmation() {
    setDetailAnalysisId(null);
    setDetailFeatures([]);
    setDetailCrops([]);
    setDetailWarnings([]);
  }

  function invalidateDetailInput() {
    detailInputVersion.current += 1;
    clearDetailConfirmation();
  }

  function updateReferenceSlots(nextSlots: ReferenceSlot[]) {
    setReferenceSlots(nextSlots);
    invalidateDetailInput();
  }

  function updateFreeText(nextFreeText: string) {
    if (nextFreeText === freeText) return;
    setFreeText(nextFreeText);
    invalidateDetailInput();
  }

  function updateSelectedRequirementIds(nextRequirementIds: string[]) {
    if (
      nextRequirementIds.length === selectedRequirementIds.length &&
      nextRequirementIds.every((id, index) => id === selectedRequirementIds[index])
    ) {
      return;
    }
    setSelectedRequirementIds(nextRequirementIds);
    invalidateDetailInput();
  }

  function storeEditorRecipe(key: string | null, recipe: EditRecipe) {
    if (!key) return;
    setEditorRecipes((current) => ({ ...current, [key]: recipe }));
  }

  function applyDetailAnalysis(analysis: DetailAnalysis) {
    setDetailAnalysisId(analysis.analysisId);
    setDetailFeatures(analysis.features);
    setDetailCrops(analysis.crops);
    setDetailWarnings(analysis.warnings);
  }

  async function analyzeDetails() {
    if (isGenerationActive || isGenerating || isAnalyzingDetails) return;

    const requestId = generateRequestId.current + 1;
    generateRequestId.current = requestId;
    const inputVersion = detailInputVersion.current;
    const startedAtMs = Date.now();
    setIsAnalyzingDetails(true);
    setDetailAnalysisStartedAtMs(startedAtMs);
    setDetailAnalysisNowMs(startedAtMs);
    setActiveStep(1);
    setGenerationError(null);

    const isCurrentDetailAnalysis = () =>
      generateRequestId.current === requestId && detailInputVersion.current === inputVersion;

    try {
      const uploadedSlots = await uploadPendingReferenceSlots(referenceSlots);
      if (!isCurrentDetailAnalysis()) return;

      setReferenceSlots(uploadedSlots);

      if (!hasReferenceSlot(uploadedSlots, "front")) {
        throw new Error("Front reference is required.");
      }

      const analysis = await analyzeReferenceDetails({
        characterSessionId,
        freeText,
        locale: currentLocale,
        referenceDescriptions: getReferenceDescriptions(uploadedSlots),
        referenceKeys: getReferenceKeys(uploadedSlots),
        requirementIds: selectedRequirementIds,
      });

      if (!isCurrentDetailAnalysis()) return;
      applyDetailAnalysis(analysis);
      const finishedAtMs = Date.now();
      const nextHistory = appendGenerationDurationSample(detailAnalysisDurationHistory, {
        durationMs: finishedAtMs - startedAtMs,
        expectedOutputCount: 1,
        finishedAt: new Date(finishedAtMs).toISOString(),
        generationMode: "detail_analysis",
        jobId: `detail-analysis-${requestId}-${startedAtMs}`,
      });
      if (nextHistory !== detailAnalysisDurationHistory) {
        setDetailAnalysisDurationHistory(nextHistory);
        writeGenerationDurationHistory(nextHistory);
      }
      setActiveStep(1);
    } catch (error) {
      if (isCurrentDetailAnalysis()) {
        setGenerationError(formatGenerationError(error));
      }
    } finally {
      if (generateRequestId.current === requestId) {
        setIsAnalyzingDetails(false);
        setDetailAnalysisStartedAtMs(null);
      }
    }
  }

  async function submitGeneration({
    detailLock = null,
    extraFreeText = "",
    extraReferenceDescriptions = [],
    extraReferenceKeys = [],
    extraReferenceSlots = [],
    generationMode,
    includeBaseReferences,
    nextStep,
  }: {
    detailLock?: DetailLockInput | null;
    extraFreeText?: string;
    extraReferenceDescriptions?: Array<{ description: string; referenceKey: string }>;
    extraReferenceKeys?: string[];
    extraReferenceSlots?: ReferenceSlot[];
    generationMode: GenerationMode;
    includeBaseReferences: boolean;
    nextStep: number;
  }) {
    const requestId = generateRequestId.current + 1;
    generateRequestId.current = requestId;
    setActiveStep(nextStep);
    setIsGenerating(true);
    setGenerationError(null);
    setJob(null);
    setActiveJobId(null);
    setSubmittingGenerationMode(generationMode);
    if (generationMode === "turnaround") {
      setTurnaroundImage(null);
    } else {
      setFrontImage(null);
      setTurnaroundImage(null);
    }

    try {
      const baseSlots = includeBaseReferences ? referenceSlots : [];
      const submittedReferenceSlots = [...baseSlots, ...extraReferenceSlots];
      const uploadedReferenceSlots = await uploadPendingReferenceSlots(submittedReferenceSlots);
      const uploadedReferenceKeys = [...getReferenceKeys(uploadedReferenceSlots), ...extraReferenceKeys];
      const referenceDescriptions = [
        ...getReferenceDescriptions(uploadedReferenceSlots),
        ...extraReferenceDescriptions,
      ];

      if (includeBaseReferences) {
        setReferenceSlots(uploadedReferenceSlots.slice(0, referenceSlots.length));
      }

      if (!hasReferenceSlot(uploadedReferenceSlots, "front")) {
        throw new Error("请先上传正脸参考图。");
      }

      const nextJob = await createGenerationJob({
        characterSessionId,
        detailLock,
        freeText: [freeText, extraFreeText].filter(Boolean).join("\n\n"),
        generationMode,
        locale: currentLocale,
        referenceDescriptions,
        referenceKeys: uploadedReferenceKeys,
        requirementIds: selectedRequirementIds,
      });

      if (generateRequestId.current !== requestId) return;
      setCharacterSessionId(nextJob.character_session_id);
      setActiveJobId(nextJob.id);
      setJob(nextJob);
      setSubmittingGenerationMode(null);
    } catch (error) {
      if (generateRequestId.current === requestId) {
        setGenerationError(formatGenerationError(error));
        setSubmittingGenerationMode(null);
      }
    } finally {
      if (generateRequestId.current === requestId) {
        setIsGenerating(false);
      }
    }
  }

  async function generateFrontDesign() {
    const detailLock: DetailLockInput = {
      sourceAnalysisId: detailAnalysisId,
      userNote: freeText,
      features: detailFeatures,
      crops: detailCrops.map((crop) => ({
        referenceKey: `detail:${crop.objectKey}`,
        kind: crop.kind,
        description: crop.description,
      })),
    };

    await submitGeneration({
      detailLock,
      extraReferenceDescriptions: getDetailReferenceDescriptions(detailCrops),
      extraReferenceKeys: getDetailReferenceKeys(detailCrops),
      generationMode: "front_design",
      includeBaseReferences: true,
      nextStep: 2,
    });
  }

  async function generateFrontDirect() {
    await submitGeneration({
      detailLock: null,
      generationMode: "front_design",
      includeBaseReferences: true,
      nextStep: 2,
    });
  }

  async function replaceDetailCrop(cropId: string, file: File) {
    const requestId = (cropReplacementRequestIds.current[cropId] ?? 0) + 1;
    cropReplacementRequestIds.current[cropId] = requestId;
    setGenerationError(null);
    try {
      const upload = await uploadReferenceFile("detail", file);
      if (cropReplacementRequestIds.current[cropId] !== requestId) return;

      setDetailCrops((current) =>
        current.map((crop) =>
          crop.id === cropId
            ? {
                ...crop,
                imageUrl: `/api/references/${upload.objectKey}`,
                objectKey: upload.objectKey,
              }
            : crop,
        ),
      );
    } catch (error) {
      if (cropReplacementRequestIds.current[cropId] !== requestId) return;
      setGenerationError(formatGenerationError(error));
    }
  }

  async function addManualDetailCrop({
    description,
    file,
    kind,
  }: {
    description: string;
    file: File;
    kind: DetailKind;
  }) {
    setGenerationError(null);
    try {
      const upload = await uploadReferenceFile("detail", file);

      setDetailCrops((current) => [
        ...current,
        {
          bbox: { x: 0, y: 0, width: 1, height: 1 },
          description,
          id: `manual-crop-${Date.now()}`,
          imageUrl: `/api/references/${upload.objectKey}`,
          kind,
          objectKey: upload.objectKey,
          sourceReferenceKey: `detail:${upload.objectKey}`,
        },
      ]);
    } catch (error) {
      setGenerationError(formatGenerationError(error));
    }
  }

  function handleDirectRefineImageSelected(file: File) {
    if (directRefineImage?.imageUrl.startsWith("blob:")) {
      URL.revokeObjectURL(directRefineImage.imageUrl);
    }
    const imageUrl = URL.createObjectURL(file);
    setDirectRefineImage({
      fileName: file.name,
      id: `${Date.now()}-${file.name}`,
      imageUrl,
    });
    setGenerationError(null);
  }

  function clearDirectRefineImage() {
    if (directRefineImage?.imageUrl.startsWith("blob:")) {
      URL.revokeObjectURL(directRefineImage.imageUrl);
    }
    setDirectRefineImage(null);
  }

  function clearFrontImage() {
    setFrontImage(null);
    setTurnaroundImage(null);
  }

  function clearTurnaroundImage() {
    setTurnaroundImage(null);
  }

  function saveEditorRecipe(key: string | null, payload: EditorImageSavePayload) {
    storeEditorRecipe(key, payload.recipe);
  }

  async function regenerateFrontFromRecipe(recipeKey: string | null, payload: EditorRegeneratePayload) {
    storeEditorRecipe(recipeKey, payload.recipe);
    await submitGeneration({
      extraFreeText: buildEditorExtraPrompt(payload),
      extraReferenceSlots: createEditorReferenceSlots(payload, "front-revision"),
      generationMode: "front_revision",
      includeBaseReferences: false,
      nextStep: 2,
    });
  }

  async function generateTurnaroundFromRecipe(recipeKey: string | null, payload: EditorRegeneratePayload) {
    storeEditorRecipe(recipeKey, payload.recipe);
    await submitGeneration({
      extraFreeText: buildEditorExtraPrompt(payload),
      extraReferenceSlots: createEditorReferenceSlots(payload, "turnaround"),
      generationMode: "turnaround",
      includeBaseReferences: false,
      nextStep: 3,
    });
  }

  async function regenerateFrontRecipe(payload: EditorRegeneratePayload) {
    await regenerateFrontFromRecipe(frontRecipeKey, payload);
  }

  async function generateTurnaroundFromFrontRecipe(payload: EditorRegeneratePayload) {
    await generateTurnaroundFromRecipe(frontRecipeKey, payload);
  }

  async function regenerateTurnaroundRecipe(payload: EditorRegeneratePayload) {
    await generateTurnaroundFromRecipe(turnaroundRecipeKey, payload);
  }

  function selectGeneratedOutput(index: number) {
    if (!latestJob) return;
    const nextImage = outputToEditorImage(latestJob, index);
    if (!nextImage) return;
    setFrontImage(nextImage);
    setDirectRefineImage(null);
    setActiveStep(2);
  }

  function renderGenerationError() {
    return generationError ? (
      <Alert color="red" icon={<IconAlertCircle size={18} />} role="alert" variant="light">
        {generationError}
      </Alert>
    ) : null;
  }

  function renderGenerateFrontButton(fullWidth = false) {
    return (
      <Button
        color="cyan"
        data-testid="generate-front-button"
        disabled={!canAnalyzeDetails}
        fullWidth={fullWidth}
        leftSection={<IconWand size={16} />}
        loading={isGenerationActive || isAnalyzingDetails}
        onClick={() => void analyzeDetails()}
        variant="filled"
      >
        {t("workflow.actions.confirmDetails")}
      </Button>
    );
  }

  function renderUploadStepActions(fullWidth = false) {
    return (
      <Group gap={1} justify="flex-end" wrap={fullWidth ? "wrap" : "nowrap"}>
        {renderGenerateFrontButton(fullWidth)}
        <Button
          color="gray"
          data-testid="direct-generate-front-button"
          disabled={!canAnalyzeDetails}
          fullWidth={fullWidth}
          leftSection={<IconWand size={16} />}
          loading={isGenerating && submittingGenerationMode === "front_design"}
          onClick={() => void generateFrontDirect()}
          variant="light"
        >
          {t("workflow.actions.generateFrontDirect")}
        </Button>
      </Group>
    );
  }

  function renderMobileStepActionBar() {
    if (!isMobileHeader) return null;

    if (activeStep === 2 && (frontImage || directRefineImage)) {
      return (
        <Box className="mobile-step-actionbar" data-testid="mobile-step-actionbar">
          <Group gap={1} wrap="nowrap">
            <Button
              color="cyan"
              data-testid="mobile-editor-regenerate"
              disabled={isGenerationActive}
              fullWidth
              leftSection={<IconWand size={16} />}
              loading={isGenerationActive}
              onClick={() => void frontEditorRef.current?.regenerate()}
              variant="filled"
            >
              重新生成正视图
            </Button>
            <Button
              color="cyan"
              data-testid="mobile-editor-secondary-regenerate"
              disabled={isGenerationActive}
              fullWidth
              leftSection={<IconWand size={16} />}
              loading={isGenerationActive}
              onClick={() => void frontEditorRef.current?.secondaryRegenerate()}
              variant="filled"
            >
              生成四视图
            </Button>
          </Group>
        </Box>
      );
    }

    if (activeStep === 3 && (turnaroundImage || directRefineImage)) {
      return (
        <Box className="mobile-step-actionbar" data-testid="mobile-step-actionbar">
          <Button
            color="cyan"
            data-testid="mobile-editor-regenerate"
            disabled={isGenerationActive}
            fullWidth
            leftSection={<IconWand size={16} />}
            loading={isGenerationActive}
            onClick={() => void turnaroundEditorRef.current?.regenerate()}
            variant="filled"
          >
            {turnaroundImage ? "重新生成四视图" : "生成四视图"}
          </Button>
        </Box>
      );
    }

    if (activeStep !== 0) return null;

    return (
      <Box className="mobile-step-actionbar" data-testid="mobile-step-actionbar">
        {renderUploadStepActions(true)}
      </Box>
    );
  }

  function renderEditor(mode: "front" | "turnaround") {
    const directEditorImage = directRefineImage ? directRefineToEditorImage(directRefineImage) : null;
    const relevantJob = isGenerationJobForEditorMode(latestJob, mode) ? latestJob : null;
    const isSubmittingRelevantGeneration =
      isGenerating &&
      (mode === "turnaround"
        ? submittingGenerationMode === "turnaround"
        : submittingGenerationMode === "front_design" ||
          submittingGenerationMode === "front_revision" ||
          submittingGenerationMode === "front_local_revision");
    const shouldHoldForGeneration = Boolean(relevantJob) || isSubmittingRelevantGeneration;
    const editorImage =
      mode === "turnaround"
        ? shouldHoldForGeneration && !turnaroundImage
          ? null
          : turnaroundImage ?? directEditorImage
        : shouldHoldForGeneration && !frontImage
          ? null
          : frontImage ?? directEditorImage;
    const isDirectEditorImage = Boolean(editorImage && directEditorImage && editorImage.id === directEditorImage.id);
    const directRecipeKey = directRefineImage ? getDirectRefineRecipeKey(directRefineImage) : null;
    const recipeKey =
      mode === "turnaround"
        ? turnaroundImage
          ? turnaroundRecipeKey
          : directRecipeKey
            ? `turnaround-source:${directRecipeKey}`
            : null
        : frontImage
          ? frontRecipeKey
          : directRecipeKey;
    const recipe = recipeKey ? editorRecipes[recipeKey] : undefined;
    const clearEditorImage = isDirectEditorImage
      ? clearDirectRefineImage
      : mode === "turnaround" && turnaroundImage
        ? clearTurnaroundImage
        : mode === "front" && frontImage
          ? clearFrontImage
          : undefined;

    if (!editorImage) {
      return (
        <Stack gap={2}>
          {relevantJob ? (
            <GenerationReview
              events={generationEvents}
              job={relevantJob}
              selectedCandidateIndex={null}
              onRefresh={() => {
                void jobQuery.refetch();
                if (shouldLoadGenerationEvents) void eventsQuery.refetch();
              }}
              onSelectCandidate={mode === "turnaround" ? () => undefined : selectGeneratedOutput}
            />
          ) : isSubmittingRelevantGeneration ? (
            <GenerationReview
              events={[]}
              job={null}
              selectedCandidateIndex={null}
              onRefresh={() => undefined}
              onSelectCandidate={() => undefined}
            />
          ) : (
            <DirectRefineUploader onImageSelected={handleDirectRefineImageSelected} />
          )}
          {renderGenerationError()}
        </Stack>
      );
    }

    return (
      <EditorWorkspace
        ref={mode === "turnaround" ? turnaroundEditorRef : frontEditorRef}
        availableTools={mode === "turnaround" ? ["annotation"] : undefined}
        candidateIndex={editorImage.index}
        imageHeight={editorImage.height}
        imageUrl={editorImage.imageUrl}
        imageWidth={editorImage.width}
        initialLandmarks={editorImage.landmarks}
        isRegenerating={isGenerationActive}
        recipe={recipe}
        regenerateLabel={mode === "turnaround" ? (turnaroundImage ? "重新生成四视图" : "生成四视图") : "重新生成正视图"}
        secondaryRegenerateLabel={mode === "front" ? "生成四视图" : undefined}
        showRegenerateActions={!isMobileHeader}
        onRecipeChange={(nextRecipe) => storeEditorRecipe(recipeKey, nextRecipe)}
        onClearImage={clearEditorImage}
        onRegenerate={
          mode === "turnaround"
            ? (payload) => generateTurnaroundFromRecipe(recipeKey, payload)
            : (payload) => regenerateFrontFromRecipe(recipeKey, payload)
        }
        onSecondaryRegenerate={mode === "front" ? (payload) => generateTurnaroundFromRecipe(recipeKey, payload) : undefined}
        onSave={(payload) => saveEditorRecipe(recipeKey, payload)}
      />
    );
  }

  function renderLocalEditor() {
    if (!directRefineImage) {
      return (
        <Stack gap={2}>
          <Box>
            <Title order={1} size="h2">
              编辑器
            </Title>
            <Text c="dimmed" mt={0.75}>
              从本地上传图片后直接进入图像编辑器。
            </Text>
          </Box>
          <DirectRefineUploader onImageSelected={handleDirectRefineImageSelected} />
        </Stack>
      );
    }

    const recipeKey = getDirectRefineRecipeKey(directRefineImage);
    const recipe = editorRecipes[recipeKey];

    return (
      <EditorWorkspace
        candidateIndex={1}
        imageUrl={directRefineImage.imageUrl}
        isRegenerating={false}
        recipe={recipe}
        onClearImage={clearDirectRefineImage}
        onRecipeChange={(nextRecipe) => storeEditorRecipe(recipeKey, nextRecipe)}
        onSave={(payload) => saveEditorRecipe(recipeKey, payload)}
      />
    );
  }

  return (
    <Box mih="100vh" style={{ background: "transparent" }}>
      <Box
        component="header"
        style={{
          alignItems: "center",
          background:
            "repeating-linear-gradient(0deg, rgba(25,31,35,0.025) 0 1px, transparent 1px 5px), var(--kb-panel)",
          border: "3px solid var(--kb-line)",
          borderRadius: 0,
          boxShadow: "var(--kb-hard-shadow)",
          display: "flex",
          margin: isMobileHeader ? "8px auto 0" : "12px auto 0",
          maxWidth: 1480,
          minHeight: isMobileHeader ? 56 : 64,
          position: "sticky",
          top: isMobileHeader ? 8 : 12,
          zIndex: 20,
        }}
      >
        <Container size="xl" style={{ width: "100%" }}>
          <Group align="center" h={isMobileHeader ? 56 : 64} gap={isMobileHeader ? 1 : 2} wrap="nowrap">
            <Group align="center" gap={isMobileHeader ? 1 : 1.5} wrap="nowrap" style={{ flex: "1 1 auto", minWidth: 0 }}>
              <Box
                component="img"
                alt="KigCraft"
                src="/logo.png"
                style={{
                  border: "2px solid var(--kb-line)",
                  borderRadius: 0,
                  display: "block",
                  flex: "0 0 auto",
                  height: isMobileHeader ? 34 : 42,
                  objectFit: "cover",
                  width: isMobileHeader ? 34 : 42,
                }}
              />
              <Box style={{ display: "flex", flexDirection: "column", justifyContent: "center", minWidth: 0, overflow: "hidden" }}>
                <Text
                  fw={800}
                  size={isMobileHeader ? "md" : "lg"}
                  style={{
                    lineHeight: 1.18,
                    marginBottom: 2,
                    overflow: "hidden",
                    paddingBottom: 1,
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  KigCraft (Beta)
                </Text>
                <Group
                  align="center"
                  gap={0.5}
                  wrap="nowrap"
                  c="dimmed"
                  style={{ lineHeight: 1.05, minWidth: 0, overflow: "hidden" }}
                >
                  <Text c="dimmed" size="xs" style={{ lineHeight: 1.05, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    @海兔SeaRabbit
                  </Text>
                  {!isMobileHeader ? <Group align="center" gap={0} wrap="nowrap">
                    <IconBrandQq size={13} color="currentColor" />
                    <Text c="dimmed" size="xs">
                      用户交流群 934715528
                    </Text>
                  </Group> : null}
                </Group>
              </Box>
            </Group>

            {isMobileHeader ? (
              <Group gap={1} ml="auto" wrap="nowrap">
                <ActionIcon
                  aria-label={t("workflow.nav.menu")}
                  data-testid="header-nav-menu"
                  onClick={(event: MouseEvent<HTMLButtonElement>) => setMenuAnchor(event.currentTarget)}
                  size="lg"
                  variant="light"
                >
                  <IconMenu2 size={20} />
                </ActionIcon>
                <Menu anchorEl={menuAnchor} open={Boolean(menuAnchor)} onClose={() => setMenuAnchor(null)}>
                  {headerNavItems.map((item) => {
                    const NavIcon = item.icon;
                    return (
                      <MenuItem
                        key={item.id}
                        data-testid={`header-nav-${item.id}`}
                        onClick={() => {
                          setActiveHeaderView(item.id);
                          setMenuAnchor(null);
                        }}
                      >
                        <Group gap={1}>
                          <NavIcon size={16} />
                          <Text size="sm">{t(item.labelKey)}</Text>
                        </Group>
                      </MenuItem>
                    );
                  })}
                  {SUPPORTED_LOCALES.map((locale) => (
                    <MenuItem
                      key={locale.value}
                      selected={locale.value === currentLocale}
                      onClick={() => void selectHeaderLocale(locale.value)}
                    >
                      <Group gap={1}>
                        <IconLanguage size={16} />
                        <Text size="sm">{locale.label}</Text>
                      </Group>
                    </MenuItem>
                  ))}
                  <MenuItem
                    component="a"
                    data-testid="header-github-link"
                    href="https://github.com/icyqwq/KigCraft"
                    rel="noreferrer"
                    target="_blank"
                  >
                    <Group gap={1}>
                      <IconBrandGithub size={16} />
                      <Text size="sm">GitHub</Text>
                    </Group>
                  </MenuItem>
                </Menu>
              </Group>
            ) : (
              <Group gap={1} ml="auto" wrap="nowrap">
                {headerNavItems.map((item) => {
                  const NavIcon = item.icon;
                  const active = activeHeaderView === item.id;
                  return (
                    <Button
                      key={item.id}
                      data-testid={`header-nav-${item.id}`}
                      h={40}
                      leftSection={<NavIcon size={16} />}
                      onClick={() => setActiveHeaderView(item.id)}
                      size="sm"
                      style={{
                        backgroundColor: active ? "var(--kb-dirty-yellow)" : "var(--kb-panel)",
                        color: active ? "var(--kb-off-white)" : "var(--kb-ink)",
                        minWidth: 124,
                      }}
                      variant={active ? "filled" : "light"}
                    >
                      {t(item.labelKey)}
                    </Button>
                  );
                })}
                <LanguageSelector />
                <Button
                  aria-label="GitHub"
                  component="a"
                  data-testid="header-github-link"
                  h={40}
                  href="https://github.com/icyqwq/KigCraft"
                  miw={40}
                  px={0}
                  rel="noreferrer"
                  target="_blank"
                  title="GitHub"
                  style={{ backgroundColor: "var(--kb-panel)", color: "var(--kb-ink)", width: 40 }}
                  variant="light"
                >
                  <IconBrandGithub size={18} />
                </Button>
              </Group>
            )}
          </Group>
        </Container>
      </Box>

      <Container py={{ base: 2, md: 3 }} size="xl" style={{ position: "relative" }}>
        {activeHeaderView === "editor" ? (
          renderLocalEditor()
        ) : activeHeaderView === "history" ? (
          <TaskHistoryPanel events={generationEvents} latestJob={latestJob} />
        ) : activeHeaderView === "about" ? (
          <AboutPanel />
        ) : (
          <Stack gap={2}>
            <Tabs
              value={activeStep}
              onChange={(_, value) => openStep(value as number)}
              variant="fullWidth"
              sx={{
                minHeight: { xs: 44, md: 54 },
                overflow: "visible",
                "& .MuiTabs-flexContainer": {
                  gap: 0,
                  overflow: "visible",
                },
                "& .MuiTabs-indicator": {
                  display: "none",
                },
              }}
            >
              {workflowSteps.map((step, index) => {
                const StepIcon = step.icon;
                const isFirstStep = index === 0;
                const isLastStep = index === workflowSteps.length - 1;
                const active = activeStep === index;
                const clipPath = isFirstStep
                  ? "polygon(0 0, calc(100% - var(--step-chevron)) 0, 100% 50%, calc(100% - var(--step-chevron)) 100%, 0 100%)"
                  : isLastStep
                    ? "polygon(0 0, 100% 0, 100% 100%, 0 100%, var(--step-chevron) 50%)"
                    : "polygon(0 0, calc(100% - var(--step-chevron)) 0, 100% 50%, calc(100% - var(--step-chevron)) 100%, 0 100%, var(--step-chevron) 50%)";
                return (
                  <Tab
                    key={step.labelKey}
                    icon={<StepIcon size={18} />}
                    iconPosition="start"
                    sx={{
                      "--step-chevron": { xs: "14px", sm: "18px", md: "24px" },
                      "--step-fill": active ? "var(--kb-dirty-yellow)" : "var(--kb-panel)",
                      "--step-hover-fill": active ? "var(--kb-muted-red)" : "var(--kb-old-paper-2)",
                      alignItems: "center",
                      background: "transparent",
                      border: 0,
                      borderRadius: 0,
                      color: "text.secondary",
                      justifyContent: "center",
                      isolation: "isolate",
                      minHeight: { xs: 44, md: 54 },
                      minWidth: 0,
                      ml: index === 0 ? 0 : { xs: "-10px", sm: "-13px", md: "-18px" },
                      overflow: "visible",
                      pl: isFirstStep ? { xs: 0.75, sm: 1, md: 2 } : { xs: 1.75, sm: 2, md: 4 },
                      pr: isLastStep ? { xs: 0.75, sm: 1, md: 2 } : { xs: 1.75, sm: 2, md: 4 },
                      position: "relative",
                      whiteSpace: "nowrap",
                      zIndex: active ? 3 : workflowSteps.length - index,
                      "&::before": {
                        background: "var(--kb-line)",
                        clipPath,
                        content: '""',
                        inset: 0,
                        pointerEvents: "none",
                        position: "absolute",
                        zIndex: -2,
                      },
                      "&::after": {
                        background: "var(--step-fill)",
                        clipPath,
                        content: '""',
                        inset: "3px",
                        pointerEvents: "none",
                        position: "absolute",
                        transition: "background-color 140ms ease",
                        zIndex: -1,
                      },
                      "& .MuiTab-iconWrapper, & .MuiTypography-root": {
                        position: "relative",
                        zIndex: 1,
                      },
                      "&.Mui-selected": {
                        color: "primary.contrastText",
                      },
                      "&:hover": {
                        background: "transparent",
                        "&::after": {
                          background: "var(--step-hover-fill)",
                        },
                      },
                      "&.Mui-disabled": {
                        opacity: 0.42,
                      },
                    }}
                    label={
                      <Box ta="left">
                        <Text
                          c={active ? "var(--kb-off-white)" : undefined}
                          fw={900}
                          size={isMobileHeader ? "xs" : "sm"}
                          style={{ whiteSpace: "nowrap" }}
                        >
                          {t(step.labelKey)}
                        </Text>
                      </Box>
                    }
                    value={index}
                  />
                );
              })}
            </Tabs>
            {renderMobileStepActionBar()}

            {activeStep === 0 && (
              <Stack gap={2}>
                <ReferenceUploader
                  headerAction={!isMobileHeader ? renderUploadStepActions() : <Box />}
                  referenceSlots={referenceSlots}
                  onReferenceSlotsChange={updateReferenceSlots}
                />
                <PromptComposer
                  freeText={freeText}
                  selectedChipIds={selectedRequirementIds}
                  onFreeTextChange={updateFreeText}
                  onSelectedChipIdsChange={updateSelectedRequirementIds}
                />
                {renderGenerationError()}
              </Stack>
            )}

            {activeStep === 1 && (
              <Stack gap={2}>
                <DetailConfirmationPanel
                  analysisElapsedMs={detailAnalysisElapsedMs}
                  analysisEtaMs={detailAnalysisEtaMs}
                  canGenerate={canSubmitConfirmedDetails()}
                  crops={detailCrops}
                  features={detailFeatures}
                  isAnalyzing={isDetailConfirmationBusy}
                  warnings={detailWarnings}
                  onBackToUpload={() => setActiveStep(0)}
                  onAddCrop={addManualDetailCrop}
                  onCropsChange={setDetailCrops}
                  onFeaturesChange={setDetailFeatures}
                  onGenerateFront={generateFrontDesign}
                  onReanalyze={analyzeDetails}
                  onReplaceCrop={replaceDetailCrop}
                />
                {renderGenerationError()}
              </Stack>
            )}

            {activeStep === 2 && <Box data-testid="workflow-front-editor-area">{renderEditor("front")}</Box>}

            {activeStep === 3 && (
              <Stack data-testid="workflow-turnaround-editor-area" gap={2}>
                {renderEditor("turnaround")}
              </Stack>
            )}
          </Stack>
        )}
      </Container>

    </Box>
  );
}
