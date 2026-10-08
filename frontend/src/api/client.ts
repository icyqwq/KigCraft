import { sequenceSessionRequest } from "./sessionRequests";
import { z } from "zod";
import { DEFAULT_LOCALE, type AppLocale } from "../i18n/locales";

const API_BASE = "/api";
const REFERENCE_UPLOAD_MAX_DIMENSION = 2048;
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

export const providerPresetSchema = z.object({
  id: z.literal("codex_full"),
  label: z.string(),
  analysis_provider: z.string(),
  analysis_model: z.string(),
  image_provider: z.string(),
  image_model: z.string(),
});

export const activeProviderPresetSchema = z.object({
  preset_id: z.literal("codex_full"),
  updated_by: z.string().default(""),
  updated_at: z.string().default(""),
});

const providerPresetsSchema = z.object({
  active: activeProviderPresetSchema,
  presets: z.array(providerPresetSchema),
});

export const providerUsageSummarySchema = z.object({
  provider: z.string(),
  calls: z.number(),
  successes: z.number(),
  failures: z.number(),
  input_tokens: z.number().nullable(),
  output_tokens: z.number().nullable(),
  total_tokens: z.number().nullable(),
  generated_images: z.number(),
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
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
  accepted_output_index: z.number().nullable().default(null),
  token_usage: tokenUsageSchema.nullable().default(null),
  outputs: z.array(generationOutputSchema),
});

const generationJobListSchema = z.object({
  jobs: z.array(generationJobSchema),
});

export const generationQueueStatusSchema = z.object({
  pending_count: z.number(),
  active_count: z.number(),
  parallelism: z.number(),
  high_load: z.boolean().default(false),
});

export const currentUserSchema = z.object({
  id: z.string(),
  email: z.string().email(),
  role: z.enum(["user", "admin"]),
  is_banned: z.boolean().default(false),
  daily_generation_remaining: z.number().int().nonnegative().default(10),
  daily_generation_reset_at: z.string().nullable().default(null),
  terms_version: z.string().nullable().default(null),
  terms_accepted_at: z.string().nullable().default(null),
  generation_quality_improvement_consent: z.boolean().default(false),
  generation_quality_improvement_consent_updated_at: z.string().nullable().default(null),
});

const okSchema = z.object({ ok: z.boolean() });
const authCaptchaConfigSchema = z.object({
  enabled: z.boolean(),
  site_key: z.string().nullable(),
});
const currentTermsVersionSchema = z.object({
  version: z.string().min(1),
});

export const adminUserSchema = z.object({
  id: z.string(),
  email: z.string().email(),
  role: z.enum(["user", "admin"]),
  is_banned: z.boolean(),
  created_at: z.string(),
  last_login_at: z.string().nullable(),
  task_count: z.number(),
  call_count: z.number(),
  generation_count: z.number().default(0),
  daily_generation_limit: z.number().int().nonnegative().default(10),
  daily_generation_limit_override: z.number().int().nonnegative().nullable().default(null),
  daily_generation_used: z.number().int().nonnegative().default(0),
  daily_generation_remaining: z.number().int().nonnegative().default(10),
  daily_generation_balance: z.number().int().nonnegative().default(10),
  daily_generation_reset_at: z.string().nullable().default(null),
});

const adminUsersSchema = z.object({
  users: z.array(adminUserSchema),
  total: z.number().int().nonnegative().default(0),
  limit: z.number().int().positive().default(50),
  offset: z.number().int().nonnegative().default(0),
  has_more: z.boolean().default(false),
  sort_by: z.string().default("created_at"),
  sort_dir: z.string().default("desc"),
});
const adminRevokeSessionsSchema = z.object({ revoked_sessions: z.number() });
const dailyGenerationQuotaResetResultSchema = z.object({
  reset_count: z.number().int().nonnegative(),
  window_start_at: z.string(),
});
const codexProfileSchema = z.object({
  id: z.string(),
  name: z.string(),
  is_active: z.boolean(),
  is_builtin: z.boolean().default(false),
  credential_files: z.array(z.string()).default([]),
  account_email: z.string().nullable().default(null),
  created_at: z.string(),
  updated_at: z.string(),
});
const codexProfilesSchema = z.object({ profiles: z.array(codexProfileSchema) });
export const codexUpdateStatusSchema = z.object({
  available: z.boolean(),
  checked_at: z.string().nullable(),
  version_before: z.string().nullable(),
  version_after: z.string().nullable(),
  succeeded: z.boolean().nullable(),
  error: z.string().nullable(),
});
const codexDeviceAuthSessionSchema = z.object({
  id: z.string(),
  profile_id: z.string(),
  status: z.string(),
  output: z.string().default(""),
  error: z.string().nullable().default(null),
  created_at: z.string(),
  completed_at: z.string().nullable().default(null),
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
  window_limit: z.number(),
  parallel_generation_limit: z.number(),
  codex_usage_min_remaining_percent: z.number(),
  daily_generation_limit: z.number().int().nonnegative().default(10),
  daily_generation_custom_limit_default: z.number().int().nonnegative().default(20),
  daily_generation_reset_hour: z.number().int().min(0).max(23).default(6),
});

export const codexRateLimitWindowSchema = z.object({
  used_percent: z.number().nullable().optional(),
  remaining_percent: z.number().nullable().optional(),
  reset_at: z.string().nullable().optional(),
  reset_after_seconds: z.number().nullable().optional(),
  window_duration_mins: z.number().nullable().optional(),
});

export const codexQuotaSchema = z.object({
  available: z.boolean().default(false),
  checked_at: z.string().nullable().optional(),
  plan_type: z.string().nullable().optional(),
  source: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
  five_hour: codexRateLimitWindowSchema.nullable().optional(),
  weekly: codexRateLimitWindowSchema.nullable().optional(),
});

export const auditUsageTrendPointSchema = z.object({
  label: z.string(),
  calls: z.number().default(0),
  succeeded: z.number().default(0),
  failed: z.number().default(0),
  queued: z.number().default(0),
  active: z.number().default(0),
  images: z.number().default(0),
  images_30d_avg: z.number().default(0),
  tokens: z.number().default(0),
});

export const codexUsageHistorySchema = z.object({
  plan_type: z.string().nullable().optional(),
  points: z.array(z.object({
    at: z.string(),
    primary_remaining_percent: z.number(),
    primary_window_minutes: z.number().nullable().optional(),
    secondary_remaining_percent: z.number().nullable().optional(),
    secondary_window_minutes: z.number().nullable().optional(),
  })).default([]),
});

export const auditSummarySchema = z.object({
  total_users: z.number(),
  active_users: z.number(),
  job_counts: z.record(z.number()),
  queue_length: z.number(),
  running_job_count: z.number().default(0),
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
  codex_quota: codexQuotaSchema.default({
    available: false,
    checked_at: null,
    plan_type: null,
    source: null,
    error: null,
    five_hour: null,
    weekly: null,
  }),
  usage_trend: z.array(auditUsageTrendPointSchema).default([]),
  latest_job_updated_at: z.string().nullable().optional(),
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
  failure_reason: z.string().nullable().default(null),
});

const auditGenerationJobsPageSchema = z.object({
  items: z.array(auditGenerationJobSchema),
  next_cursor: z.string().nullable().optional(),
  has_more: z.boolean().default(false),
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
export type ProviderPreset = z.infer<typeof providerPresetSchema>;
export type ActiveProviderPreset = z.infer<typeof activeProviderPresetSchema>;
export type ProviderUsageSummary = z.infer<typeof providerUsageSummarySchema>;
export type PromptChip = z.infer<typeof legacyPromptChipSchema>;
export type GenerationJob = z.infer<typeof generationJobSchema>;
export type GenerationQueueStatus = z.infer<typeof generationQueueStatusSchema>;
export type GenerationLandmarks = z.infer<typeof generationLandmarksSchema>;
export type TokenUsage = z.infer<typeof tokenUsageSchema>;
export type JobEvent = z.infer<typeof jobEventSchema>;
export type CurrentUser = z.infer<typeof currentUserSchema>;
export type AdminUser = z.infer<typeof adminUserSchema>;
export type AdminUsersPage = z.infer<typeof adminUsersSchema>;
export type AdminRevokeSessionsResult = z.infer<typeof adminRevokeSessionsSchema>;
export type DailyGenerationQuotaResetResult = z.infer<typeof dailyGenerationQuotaResetResultSchema>;
export type CodexProfile = z.infer<typeof codexProfileSchema>;
export type CodexUpdateStatus = z.infer<typeof codexUpdateStatusSchema>;
export type CodexDeviceAuthSession = z.infer<typeof codexDeviceAuthSessionSchema>;
export type AuthPurpose = "register" | "login" | "reset_password";
export type AuthCaptchaConfig = z.infer<typeof authCaptchaConfigSchema>;
export type CurrentTermsVersion = z.infer<typeof currentTermsVersionSchema>;
export type ReferenceUpload = z.infer<typeof referenceUploadSchema>;
export type DetailKind = z.infer<typeof detailKindSchema>;
export type DetailFeature = z.infer<typeof detailFeatureSchema>;
export type DetailCrop = z.infer<typeof detailCropSchema>;
export type DetailAnalysis = z.infer<typeof detailAnalysisSchema>;
export type QuotaPolicy = z.infer<typeof quotaPolicySchema>;
export type CodexQuota = z.infer<typeof codexQuotaSchema>;
export type CodexUsageHistory = z.infer<typeof codexUsageHistorySchema>;
export type AuditUsageTrendPoint = z.infer<typeof auditUsageTrendPointSchema>;
export type AuditGenerationJobsPage = z.infer<typeof auditGenerationJobsPageSchema>;
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
    readonly retryAfterSeconds?: number,
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
      const retryAfterHeader = response.headers.get("Retry-After");
      const retryAfterSeconds = retryAfterHeader ? Number.parseInt(retryAfterHeader, 10) : undefined;
      throw new ApiError(
        `API request failed: ${response.status}`,
        response.status,
        detail,
        Number.isFinite(retryAfterSeconds) ? retryAfterSeconds : undefined,
      );
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

export function getProviderPresets(): Promise<{ active: ActiveProviderPreset; presets: ProviderPreset[] }> {
  return requestJson("/admin/provider-presets", providerPresetsSchema);
}

export function setActiveProviderPreset(presetId: ProviderPreset["id"]): Promise<ActiveProviderPreset> {
  return requestJson("/admin/provider-presets/active", activeProviderPresetSchema, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preset_id: presetId }),
  });
}

export function getProviderUsage(): Promise<ProviderUsageSummary[]> {
  return requestJson("/admin/provider-usage", z.array(providerUsageSummarySchema));
}

export function getCurrentUser(signal?: AbortSignal): Promise<CurrentUser> {
  return sequenceSessionRequest(() => requestJson("/auth/me", currentUserSchema, { signal }), signal);
}

export function getAuthCaptchaConfig(): Promise<AuthCaptchaConfig> {
  return requestJson("/auth/captcha", authCaptchaConfigSchema);
}

export function getCurrentTermsVersion(): Promise<CurrentTermsVersion> {
  return requestJson("/auth/terms-version", currentTermsVersionSchema);
}

export function sendAuthCode(input: {
  email: string;
  purpose: AuthPurpose;
  captchaToken?: string | null;
}): Promise<{ ok: boolean }> {
  return requestJson("/auth/send-code", okSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: input.email,
      purpose: input.purpose,
      captcha_token: input.captchaToken,
    }),
  });
}

export function registerAccount(input: {
  email: string;
  code: string;
  password: string;
  termsAccepted: boolean;
  termsVersion: string;
  captchaToken?: string | null;
}): Promise<CurrentUser> {
  return sequenceSessionRequest(() => requestJson("/auth/register", currentUserSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: input.email,
      code: input.code,
      password: input.password,
      terms_accepted: input.termsAccepted,
      terms_version: input.termsVersion,
      captcha_token: input.captchaToken,
    }),
  }));
}

export function updatePrivacyPreferences(consent: boolean): Promise<CurrentUser> {
  return requestJson("/auth/privacy-preferences", currentUserSchema, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ generation_quality_improvement_consent: consent }),
  });
}

export function loginWithPassword(input: {
  email: string;
  password: string;
  captchaToken?: string | null;
}): Promise<CurrentUser> {
  return sequenceSessionRequest(() => requestJson("/auth/login/password", currentUserSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: input.email,
      password: input.password,
      captcha_token: input.captchaToken,
    }),
  }));
}

export function loginWithCode(input: {
  email: string;
  code: string;
  captchaToken?: string | null;
}): Promise<CurrentUser> {
  return sequenceSessionRequest(() => requestJson("/auth/login/code", currentUserSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: input.email,
      code: input.code,
      captcha_token: input.captchaToken,
    }),
  }));
}

export function resetPassword(input: {
  email: string;
  code: string;
  password: string;
  captchaToken?: string | null;
}): Promise<{ ok: boolean }> {
  return sequenceSessionRequest(() => requestJson("/auth/reset-password", okSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: input.email,
      code: input.code,
      password: input.password,
      captcha_token: input.captchaToken,
    }),
  }));
}

export function changePassword(input: {
  currentPassword: string;
  newPassword: string;
}): Promise<{ ok: boolean }> {
  return sequenceSessionRequest(() => requestJson("/auth/change-password", okSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: input.currentPassword,
      new_password: input.newPassword,
    }),
  }));
}

export function logoutAccount(): Promise<{ ok: boolean }> {
  return sequenceSessionRequest(() => requestJson("/auth/logout", okSchema, { method: "POST" }));
}

export function listAdminUsersPage(input: {
  query?: string;
  limit?: number;
  offset?: number;
  sortBy?: "created_at" | "generation_count" | "remaining_quota";
  sortDir?: "asc" | "desc";
} = {}): Promise<AdminUsersPage> {
  const params = new URLSearchParams();
  if (input.query?.trim()) params.set("query", input.query.trim());
  if (input.limit) params.set("limit", String(input.limit));
  if (input.offset) params.set("offset", String(input.offset));
  if (input.sortBy) params.set("sort_by", input.sortBy);
  if (input.sortDir) params.set("sort_dir", input.sortDir);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson(`/admin/users${suffix}`, adminUsersSchema);
}

export function listAdminUsers(query = ""): Promise<AdminUser[]> {
  return listAdminUsersPage({ query }).then((body) => body.users);
}

export function updateAdminUserBan(userId: string, isBanned: boolean): Promise<AdminUser> {
  return requestJson(`/admin/users/${encodeURIComponent(userId)}/ban`, adminUserSchema, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_banned: isBanned }),
  });
}

export function updateAdminUserRole(userId: string, role: "user" | "admin"): Promise<AdminUser> {
  return requestJson(`/admin/users/${encodeURIComponent(userId)}/role`, adminUserSchema, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
}

export function revokeAdminUserSessions(userId: string): Promise<AdminRevokeSessionsResult> {
  return requestJson(`/admin/users/${encodeURIComponent(userId)}/revoke-sessions`, adminRevokeSessionsSchema, {
    method: "POST",
  });
}

export function resetDailyGenerationQuota(): Promise<DailyGenerationQuotaResetResult> {
  return requestJson("/admin/quota/daily-generation/reset", dailyGenerationQuotaResetResultSchema, {
    method: "POST",
  });
}

export function resetAdminUserDailyGenerationQuota(userId: string): Promise<DailyGenerationQuotaResetResult> {
  return requestJson(
    `/admin/users/${encodeURIComponent(userId)}/quota/daily-generation/reset`,
    dailyGenerationQuotaResetResultSchema,
    { method: "POST" },
  );
}

export function updateAdminUserDailyGenerationLimit(userId: string, limit: number | null): Promise<AdminUser> {
  return requestJson(
    `/admin/users/${encodeURIComponent(userId)}/quota/daily-generation-limit`,
    adminUserSchema,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit }),
    },
  );
}

export function adjustAdminUserDailyGenerationQuota(userId: string, delta: number): Promise<AdminUser> {
  return requestJson(
    `/admin/users/${encodeURIComponent(userId)}/quota/daily-generation/adjust`,
    adminUserSchema,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delta }),
    },
  );
}

export function listCodexProfiles(): Promise<CodexProfile[]> {
  return requestJson("/admin/codex-profiles", codexProfilesSchema).then((body) => body.profiles);
}

export function createCodexProfile(input: { id: string; name: string }): Promise<CodexProfile> {
  return requestJson("/admin/codex-profiles", codexProfileSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: input.id, name: input.name }),
  });
}

export function importCodexProfileCredentials(profileId: string, files: Record<string, string>): Promise<CodexProfile> {
  return requestJson(`/admin/codex-profiles/${encodeURIComponent(profileId)}/credentials`, codexProfileSchema, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files }),
  });
}

export function renameCodexProfile(input: { profileId: string; name: string }): Promise<CodexProfile> {
  return requestJson(`/admin/codex-profiles/${encodeURIComponent(input.profileId)}`, codexProfileSchema, {
    body: JSON.stringify({ name: input.name }),
    method: "PATCH",
  });
}

export function activateCodexProfile(profileId: string): Promise<CodexProfile> {
  return requestJson(`/admin/codex-profiles/${encodeURIComponent(profileId)}/activate`, codexProfileSchema, {
    method: "POST",
  });
}

export function deleteCodexProfile(profileId: string): Promise<{ ok: boolean }> {
  return requestJson(`/admin/codex-profiles/${encodeURIComponent(profileId)}`, z.object({ ok: z.boolean() }), {
    method: "DELETE",
  });
}

export function startCodexProfileDeviceAuth(profileId: string): Promise<CodexDeviceAuthSession> {
  return requestJson(`/admin/codex-profiles/${encodeURIComponent(profileId)}/device-auth`, codexDeviceAuthSessionSchema, {
    method: "POST",
  });
}

export function getCodexUpdateStatus(): Promise<CodexUpdateStatus> {
  return requestJson("/admin/codex-profiles/update-status", codexUpdateStatusSchema);
}

export function startCodexDeviceAuth(): Promise<CodexDeviceAuthSession> {
  return requestJson("/admin/codex-profiles/device-auth", codexDeviceAuthSessionSchema, { method: "POST" });
}

export function getCodexProfileDeviceAuth(profileId: string, sessionId: string): Promise<CodexDeviceAuthSession> {
  return requestJson(
    `/admin/codex-profiles/${encodeURIComponent(profileId)}/device-auth/${encodeURIComponent(sessionId)}`,
    codexDeviceAuthSessionSchema,
  );
}

export function cancelCodexProfileDeviceAuth(profileId: string, sessionId: string): Promise<CodexDeviceAuthSession> {
  return requestJson(
    `/admin/codex-profiles/${encodeURIComponent(profileId)}/device-auth/${encodeURIComponent(sessionId)}/cancel`,
    codexDeviceAuthSessionSchema,
    { method: "POST" },
  );
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

export type CreateGenerationJobInput = {
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

export function listGenerationJobs(input: { status?: string; generationMode?: string } = {}): Promise<GenerationJob[]> {
  const params = new URLSearchParams();
  if (input.status) params.set("status", input.status);
  if (input.generationMode) params.set("generation_mode", input.generationMode);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson(`/generation/jobs${suffix}`, generationJobListSchema).then((body) => body.jobs);
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

export function getGenerationQueueStatus(): Promise<GenerationQueueStatus> {
  return requestJson("/generation/queue-status", generationQueueStatusSchema);
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

export function cancelGenerationJob(jobId: string): Promise<GenerationJob> {
  return requestJson(`/generation/jobs/${encodeURIComponent(jobId)}/cancel`, generationJobSchema, {
    method: "POST",
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

export function getCodexUsageHistory(hours: number): Promise<CodexUsageHistory> {
  return requestJson(`/audit/codex-usage-history?hours=${encodeURIComponent(String(hours))}`, codexUsageHistorySchema);
}

export function getAuditGenerationJobsPage(params: {
  cursor?: string;
  limit?: number;
} = {}): Promise<AuditGenerationJobsPage> {
  const search = new URLSearchParams();
  if (params.limit) search.set("limit", String(params.limit));
  if (params.cursor) search.set("cursor", params.cursor);
  const query = search.toString();
  return requestJson(
    `/audit/generation-jobs${query ? `?${query}` : ""}`,
    auditGenerationJobsPageSchema,
  );
}

export function getAuditGenerationJobs(): Promise<AuditGenerationJob[]> {
  return requestJson("/admin/generation-jobs", z.array(auditGenerationJobSchema));
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


export function revokeOtherSessions() {
  return requestJson("/auth/sessions/revoke-others", adminRevokeSessionsSchema, {method:"POST"});
}
