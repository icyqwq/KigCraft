import { z } from "zod";
import { DEFAULT_LOCALE, type AppLocale } from "../i18n/locales";

const API_BASE = "/api";
const REFERENCE_UPLOAD_MAX_DIMENSION = 600;
const REFERENCE_UPLOAD_JPEG_QUALITY = 0.9;
const DETAIL_ANALYSIS_REQUEST_TIMEOUT_MS = 250_000;

export const requirementOptionSchema = z.object({
  id: z.string(),
  group: z.string(),
  label: z.string(),
  description: z.string(),
  prompt_text: z.string(),
  sort_order: z.number(),
});

export const legacyPromptChipSchema = z.object({
  id: z.string(),
  category: z.string(),
  label: z.string(),
  text: z.string(),
  sort_order: z.number(),
}).strict();

export const landmarkPointSchema = z.object({
  x: z.number(),
  y: z.number(),
});

export const generationLandmarksSchema = z.object({
  leftEye: landmarkPointSchema,
  rightEye: landmarkPointSchema,
  chin: landmarkPointSchema,
  jawLeft: landmarkPointSchema,
  jawRight: landmarkPointSchema,
});

export const generationOutputSchema = z.object({
  index: z.number(),
  object_key: z.string(),
  image_url: z.string(),
  width: z.number(),
  height: z.number(),
  landmarks: generationLandmarksSchema.nullish(),
});

export const tokenUsageSchema = z.object({
  input_tokens: z.number().nullable().default(null),
  cached_input_tokens: z.number().nullable().default(null),
  output_tokens: z.number().nullable().default(null),
  reasoning_output_tokens: z.number().nullable().default(null),
  total_tokens: z.number().nullable().default(null),
});

export const generationJobSchema = z.object({
  id: z.string(),
  character_session_id: z.string(),
  generation_mode: z.string().default("front_design"),
  expected_output_count: z.number().default(1),
  status: z.string(),
  progress: z.number(),
  queue_position: z.number().nullable(),
  phase_label: z.string(),
  provider: z.string(),
  accepted_output_index: z.number().nullable().default(null),
  token_usage: tokenUsageSchema.nullable().default(null),
  outputs: z.array(generationOutputSchema),
});

const legacyGenerationOutputSchema = z.object({
  index: z.number(),
  object_key: z.string(),
  width: z.number(),
  height: z.number(),
}).strict();

const legacyGenerationJobSchema = z
  .object({
    id: z.string(),
    project_id: z.string(),
    status: z.string(),
    progress: z.number(),
    outputs: z.array(legacyGenerationOutputSchema),
  })
  .strict()
  .transform((job) =>
    generationJobSchema.parse({
      id: job.id,
      character_session_id: job.project_id,
      status: job.status,
      progress: job.progress,
      queue_position: null,
      phase_label: job.status,
      provider: "legacy",
      token_usage: null,
      outputs: job.outputs.map((output) => ({
        ...output,
        image_url: output.object_key,
      })),
    }),
  );

export const jobEventSchema = z.object({
  sequence: z.number(),
  type: z.string(),
  progress: z.number(),
  message: z.string(),
  created_at: z.string(),
  payload: z.record(z.unknown()).default({}),
});

export const referenceUploadSchema = z
  .object({
    object_key: z.string(),
    file_name: z.string(),
  })
  .transform((upload) => ({
    objectKey: upload.object_key,
    fileName: upload.file_name,
  }));

export const detailKindSchema = z.enum([
  "hair",
  "eyes",
  "expression",
  "headwear",
  "accessory",
  "ears",
  "requirement",
  "outfit",
  "color",
  "avoid",
  "other",
]);

export const detailFeatureSchema = z.object({
  id: z.string(),
  kind: detailKindSchema,
  label: z.string().default(""),
  description: z.string(),
  confidence: z.number().nullable().optional(),
});

const detailCropBboxSchema = z.object({
  x: z.number(),
  y: z.number(),
  width: z.number(),
  height: z.number(),
});

export const detailCropSchema = z
  .object({
    id: z.string(),
    kind: detailKindSchema,
    description: z.string(),
    source_reference_key: z.string(),
    bbox: detailCropBboxSchema,
    object_key: z.string(),
    image_url: z.string(),
  })
  .transform((crop) => ({
    id: crop.id,
    kind: crop.kind,
    description: crop.description,
    sourceReferenceKey: crop.source_reference_key,
    bbox: crop.bbox,
    objectKey: crop.object_key,
    imageUrl: crop.image_url,
  }));

export const detailAnalysisSchema = z
  .object({
    analysis_id: z.string(),
    features: z.array(detailFeatureSchema),
    crops: z.array(detailCropSchema),
    warnings: z.array(z.string()).default([]),
  })
  .transform((analysis) => ({
    analysisId: analysis.analysis_id,
    features: analysis.features,
    crops: analysis.crops,
    warnings: analysis.warnings,
  }));

export const quotaPolicySchema = z.object({
  window_hours: z.number(),
  normal_window_limit: z.number(),
  premium_unlimited: z.boolean(),
  parallel_generation_limit: z.number(),
});

export const auditSummarySchema = z.object({
  total_users: z.number(),
  active_users: z.number(),
  job_counts: z.record(z.number()),
  queue_length: z.number(),
  quota_policy: quotaPolicySchema,
  total_calls: z.number(),
  success_rate: z.number(),
  failure_rate: z.number(),
  parallel_slots_used: z.number(),
  token_usage: z
    .object({
      cached_input_tokens: z.number().nullable().default(null),
      input_tokens: z.number().nullable().default(null),
      jobs_with_usage: z.number().default(0),
      output_tokens: z.number().nullable().default(null),
      reasoning_output_tokens: z.number().nullable().default(null),
      total_tokens: z.number().nullable().default(null),
    })
    .default({
      cached_input_tokens: null,
      input_tokens: null,
      jobs_with_usage: 0,
      output_tokens: null,
      reasoning_output_tokens: null,
      total_tokens: null,
    }),
  image_usage: z
    .object({
      cached_input_tokens_per_image: z.number().default(0),
      generated_images: z.number().default(0),
      images_with_token_usage: z.number().default(0),
      input_tokens_per_image: z.number().default(0),
      jobs_with_outputs: z.number().default(0),
      output_tokens_per_image: z.number().default(0),
      reasoning_output_tokens_per_image: z.number().default(0),
      total_tokens_per_image: z.number().default(0),
    })
    .default({
      cached_input_tokens_per_image: 0,
      generated_images: 0,
      images_with_token_usage: 0,
      input_tokens_per_image: 0,
      jobs_with_outputs: 0,
      output_tokens_per_image: 0,
      reasoning_output_tokens_per_image: 0,
      total_tokens_per_image: 0,
    }),
});

export const auditGenerationReferenceSchema = z.object({
  reference_key: z.string(),
  image_url: z.string().nullable().default(null),
  kind: z.string().nullable().default(null),
  description: z.string().nullable().default(null),
});

export const auditGenerationOutputSchema = z.object({
  index: z.number(),
  object_key: z.string(),
  image_url: z.string(),
  width: z.number(),
  height: z.number(),
});

export const auditGenerationJobSchema = z.object({
  id: z.string(),
  character_session_id: z.string(),
  generation_mode: z.string(),
  status: z.string(),
  progress: z.number(),
  phase_label: z.string(),
  provider: z.string(),
  created_at: z.string().nullable().default(null),
  updated_at: z.string().nullable().default(null),
  user_notes: z.string().default(""),
  user_requirements: z.string().default(""),
  requirement_ids: z.array(z.string()).default([]),
  references: z.array(auditGenerationReferenceSchema).default([]),
  outputs: z.array(auditGenerationOutputSchema).default([]),
  token_usage: z
    .object({
      cached_input_tokens: z.number().default(0),
      input_tokens: z.number().default(0),
      jobs_with_usage: z.number().default(0),
      output_tokens: z.number().default(0),
      reasoning_output_tokens: z.number().default(0),
      total_tokens: z.number().default(0),
    })
    .nullable()
    .default(null),
});

const auditGenerationJobsSchema = z.array(auditGenerationJobSchema);

const auditSessionSchema = z.object({
  authenticated: z.boolean(),
});

const jsonRecordSchema: z.ZodType<Record<string, unknown>> = z.record(z.unknown());

export const albumItemSchema = z.object({
  id: z.string(),
  image_url: z.string(),
  created_at: z.string(),
  recipe: jsonRecordSchema.nullish(),
  metadata: jsonRecordSchema.nullish(),
});

const albumItemsSchema = z.array(albumItemSchema);
const jobEventsSchema = z.array(jobEventSchema);

const legacyJobEventSchema = z.object({
  type: z.string(),
  progress: z.number(),
}).strict();

const legacyJobEventsSchema = z.array(legacyJobEventSchema).transform((events) =>
  events.map((event, index) =>
    jobEventSchema.parse({
      sequence: index + 1,
      type: event.type,
      progress: event.progress,
      message: event.type,
      created_at: "",
      payload: {},
    }),
  ),
);

export type RequirementOption = z.infer<typeof requirementOptionSchema>;
export type PromptChip = z.infer<typeof legacyPromptChipSchema>;
export type GenerationJob = z.infer<typeof generationJobSchema>;
export type GenerationLandmarks = z.infer<typeof generationLandmarksSchema>;
export type TokenUsage = z.infer<typeof tokenUsageSchema>;
export type JobEvent = z.infer<typeof jobEventSchema>;
export type ReferenceUpload = z.infer<typeof referenceUploadSchema>;
export type DetailKind = z.infer<typeof detailKindSchema>;
export type DetailFeature = z.infer<typeof detailFeatureSchema>;
export type DetailCrop = z.infer<typeof detailCropSchema>;
export type DetailAnalysis = z.infer<typeof detailAnalysisSchema>;
export type QuotaPolicy = z.infer<typeof quotaPolicySchema>;
export type AuditSummary = z.infer<typeof auditSummarySchema>;
export type AuditGenerationJob = z.infer<typeof auditGenerationJobSchema>;
export type AlbumItem = z.infer<typeof albumItemSchema>;

export type GenerationMode = "front_design" | "front_revision" | "front_local_revision" | "turnaround";

export type SaveAlbumItemInput = {
  image_url: string;
  recipe?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
};

export type SaveAlbumImageFileInput = {
  file: File;
  recipe?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
};

export type DetailLockInput = {
  sourceAnalysisId?: string | null;
  userNote: string;
  features: DetailFeature[];
  crops: Array<{
    referenceKey: string;
    kind: DetailKind;
    description: string;
  }>;
};

export type AnalyzeReferenceDetailsInput = {
  characterSessionId: string | null;
  freeText: string;
  locale: AppLocale;
  requirementIds: string[];
  referenceKeys: string[];
  referenceDescriptions: Array<{
    description: string;
    referenceKey: string;
  }>;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

async function requestJson<T>(
  url: string,
  schema: z.ZodType<T, z.ZodTypeDef, unknown>,
  init?: RequestInit,
  options?: { timeoutDetail?: string; timeoutMs?: number },
): Promise<T> {
  const body = await requestRawJson(url, init, options);
  return schema.parse(body);
}

async function requestRawJson(
  url: string,
  init?: RequestInit,
  options?: { timeoutDetail?: string; timeoutMs?: number },
): Promise<unknown> {
  const timeoutMs = options?.timeoutMs;
  const controller = timeoutMs ? new AbortController() : null;
  const timeoutId = controller
    ? window.setTimeout(() => controller.abort(), timeoutMs)
    : null;
  try {
    const response = await fetch(`${API_BASE}${url}`, controller ? { ...init, signal: controller.signal } : init);
    const body = await readJson(response);
    if (!response.ok) {
      const detail =
        typeof body === "object" && body && "detail" in body
          ? (body as { detail?: unknown }).detail
          : body;
      throw new ApiError(`API request failed: ${response.status}`, response.status, detail);
    }
    return body;
  } catch (error) {
    if (
      options?.timeoutDetail &&
      error instanceof DOMException &&
      error.name === "AbortError"
    ) {
      throw new ApiError("API request timed out", 504, options.timeoutDetail);
    }
    throw error;
  } finally {
    if (timeoutId !== null) window.clearTimeout(timeoutId);
  }
}

export function getRequirementOptions(): Promise<RequirementOption[]> {
  return requestJson("/prompts/requirements", z.array(requirementOptionSchema));
}

export function analyzeReferenceDetails(input: AnalyzeReferenceDetailsInput): Promise<DetailAnalysis> {
  return requestJson("/generation/detail-analysis", detailAnalysisSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      character_session_id: input.characterSessionId,
      free_text: input.freeText,
      locale: input.locale,
      requirement_ids: input.requirementIds,
      reference_keys: input.referenceKeys,
      reference_descriptions: input.referenceDescriptions.map((item) => ({
        description: item.description,
        reference_key: item.referenceKey,
      })),
    }),
  }, {
    timeoutDetail: "detail_analysis_request_timeout",
    timeoutMs: DETAIL_ANALYSIS_REQUEST_TIMEOUT_MS,
  });
}

type CreateGenerationJobInput = {
  characterSessionId?: string | null;
  detailLock?: DetailLockInput | null;
  freeText: string;
  generationMode?: GenerationMode;
  locale?: AppLocale;
  referenceDescriptions?: Array<{
    description: string;
    referenceKey: string;
  }>;
  requirementIds: string[];
  referenceKeys: string[];
};

export type CreateLocalRevisionJobInput = {
  baseImageBlob: Blob;
  characterSessionId?: string | null;
  editNote: string;
  locale?: AppLocale;
  maskImageBlob: Blob;
  recipe?: Record<string, unknown> | null;
  referenceDescriptions?: Array<{
    description: string;
    referenceKey: string;
  }>;
  selectedReferenceKeys: string[];
  uploadedReferences?: Array<{
    description: string;
    file: File;
  }>;
};

type LegacyCreateGenerationJobInput = {
  projectId: string;
  freeText: string;
  chipIds: string[];
  referenceKeys: string[];
};

export function createGenerationJob(input: CreateGenerationJobInput): Promise<GenerationJob>;
export function createGenerationJob(input: LegacyCreateGenerationJobInput): Promise<GenerationJob>;
export function createGenerationJob(input: CreateGenerationJobInput | LegacyCreateGenerationJobInput): Promise<GenerationJob> {
  if ("projectId" in input) {
    return requestJson(
      `/generation/projects/${encodeURIComponent(input.projectId)}/jobs`,
      legacyGenerationJobSchema,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: input.projectId,
          free_text: input.freeText,
          chip_ids: input.chipIds,
          reference_keys: input.referenceKeys,
        }),
      },
    );
  }

  return requestJson("/generation/jobs", generationJobSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      character_session_id: input.characterSessionId,
      ...(input.detailLock
        ? {
            detail_lock: {
              source_analysis_id: input.detailLock.sourceAnalysisId ?? null,
              user_note: input.detailLock.userNote,
              features: input.detailLock.features,
              crops: input.detailLock.crops.map((crop) => ({
                reference_key: crop.referenceKey,
                kind: crop.kind,
                description: crop.description,
              })),
            },
          }
        : {}),
      free_text: input.freeText,
      generation_mode: input.generationMode ?? "front_design",
      locale: input.locale ?? DEFAULT_LOCALE,
      reference_descriptions: input.referenceDescriptions?.map((item) => ({
        description: item.description,
        reference_key: item.referenceKey,
      })) ?? [],
      requirement_ids: input.requirementIds,
      reference_keys: input.referenceKeys,
    }),
  });
}

export async function createLocalRevisionJob(input: CreateLocalRevisionJobInput): Promise<GenerationJob> {
  const body = new FormData();
  body.append(
    "metadata",
    JSON.stringify({
      character_session_id: input.characterSessionId ?? null,
      edit_note: input.editNote,
      locale: input.locale ?? DEFAULT_LOCALE,
      recipe: input.recipe ?? {},
      reference_descriptions:
        input.referenceDescriptions?.map((item) => ({
          description: item.description,
          reference_key: item.referenceKey,
        })) ?? [],
      selected_reference_keys: input.selectedReferenceKeys,
      uploaded_reference_descriptions: input.uploadedReferences?.map((item) => item.description) ?? [],
    }),
  );
  body.append("base_image", input.baseImageBlob, "base.png");
  body.append("mask_image", input.maskImageBlob, "mask.png");
  for (const reference of input.uploadedReferences ?? []) {
    body.append("reference_files", await normalizeReferenceUploadFile(reference.file));
  }

  return requestJson("/generation/local-revision-jobs", generationJobSchema, {
    method: "POST",
    body,
  });
}

export async function getGenerationJob(jobId: string): Promise<GenerationJob> {
  const body = await requestRawJson(`/generation/jobs/${jobId}`);
  const futureJob = generationJobSchema.safeParse(body);
  if (futureJob.success) return futureJob.data;
  return legacyGenerationJobSchema.parse(body);
}

export async function getGenerationEvents(jobId: string): Promise<JobEvent[]> {
  const body = await requestRawJson(`/generation/jobs/${jobId}/events`);
  const futureEvents = jobEventsSchema.safeParse(body);
  if (futureEvents.success) return futureEvents.data;
  return legacyJobEventsSchema.parse(body);
}

export function acceptGenerationCandidate(
  jobId: string,
  outputIndex: number,
): Promise<GenerationJob> {
  return requestJson(`/generation/jobs/${encodeURIComponent(jobId)}/accept`, generationJobSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ output_index: outputIndex }),
  });
}

export async function uploadReferenceFile(kind: string, file: File): Promise<ReferenceUpload> {
  const uploadFile = await normalizeReferenceUploadFile(file);
  const body = new FormData();
  body.append("kind", kind);
  body.append("file", uploadFile);

  return requestJson("/references", referenceUploadSchema, {
    method: "POST",
    body,
  });
}

async function normalizeReferenceUploadFile(file: File): Promise<File> {
  if (!file.type.startsWith("image/")) return file;

  try {
    const image = await loadImageElement(file);
    const sourceWidth = image.naturalWidth || image.width;
    const sourceHeight = image.naturalHeight || image.height;
    const longestSide = Math.max(sourceWidth, sourceHeight);

    if (!sourceWidth || !sourceHeight || longestSide <= 0) return file;

    const scale = Math.min(1, REFERENCE_UPLOAD_MAX_DIMENSION / longestSide);
    const targetWidth = Math.max(1, Math.round(sourceWidth * scale));
    const targetHeight = Math.max(1, Math.round(sourceHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = targetWidth;
    canvas.height = targetHeight;
    const context = canvas.getContext("2d");
    if (!context) return file;

    context.fillStyle = "#fff";
    context.fillRect(0, 0, targetWidth, targetHeight);
    context.drawImage(image, 0, 0, targetWidth, targetHeight);

    const blob = await canvasToBlob(canvas, "image/jpeg", REFERENCE_UPLOAD_JPEG_QUALITY);
    return new File([blob], replaceImageExtension(file.name, ".jpg"), {
      lastModified: file.lastModified,
      type: "image/jpeg",
    });
  } catch {
    return file;
  }
}

function loadImageElement(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();

    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Reference image decode failed"));
    };
    image.src = url;
  });
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) resolve(blob);
        else reject(new Error("Reference image compression failed"));
      },
      type,
      quality,
    );
  });
}

function replaceImageExtension(fileName: string, extension: string) {
  const stem = fileName.replace(/\.[A-Za-z0-9]+$/, "");
  return `${stem || "reference"}${extension}`;
}

export async function getPromptChips(): Promise<PromptChip[]> {
  return requestJson("/prompts/chips", z.array(legacyPromptChipSchema));
}

export function getAuditSummary(): Promise<AuditSummary> {
  return requestJson("/audit/summary", auditSummarySchema);
}

export function getAuditGenerationJobs(): Promise<AuditGenerationJob[]> {
  return requestJson("/audit/generation-jobs", auditGenerationJobsSchema);
}

export function getAuditSession(): Promise<z.infer<typeof auditSessionSchema>> {
  return requestJson("/audit/session", auditSessionSchema);
}

export function loginAudit(password: string): Promise<z.infer<typeof auditSessionSchema>> {
  return requestJson("/audit/login", auditSessionSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

export function logoutAudit(): Promise<{ ok: boolean }> {
  return requestJson("/audit/logout", z.object({ ok: z.boolean() }), {
    method: "POST",
  });
}

export function updateQuotaPolicy(policy: QuotaPolicy): Promise<QuotaPolicy> {
  return requestJson("/audit/quota-policy", quotaPolicySchema, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(policy),
  });
}

export function saveAlbumItem(input: SaveAlbumItemInput): Promise<AlbumItem> {
  return requestJson("/album/items", albumItemSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function saveAlbumImageFile(input: SaveAlbumImageFileInput): Promise<AlbumItem> {
  const body = new FormData();
  body.append("file", input.file);
  if (input.recipe) {
    body.append("recipe", JSON.stringify(input.recipe));
  }
  if (input.metadata) {
    body.append("metadata", JSON.stringify(input.metadata));
  }

  return requestJson("/album/items/file", albumItemSchema, {
    method: "POST",
    body,
  });
}

export function listAlbumItems(): Promise<AlbumItem[]> {
  return requestJson("/album/items", albumItemsSchema);
}

