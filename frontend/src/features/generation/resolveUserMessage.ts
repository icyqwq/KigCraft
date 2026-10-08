import type { TFunction } from "i18next";
import i18n from "../../i18n";

type MessageParams = Record<string, string | number>;
type TranslateFn = TFunction<"translation", undefined>;

const STATUS_LABEL_CODES = new Set([
  "accepted",
  "cancelled",
  "candidate_ready",
  "codex_generating",
  "failed",
  "generating",
  "generation_complete",
  "preparing_references",
  "queued",
  "running",
  "saving_outputs",
  "succeeded",
]);

function formatWaitTime(seconds: number, t: TranslateFn): string {
  if (seconds < 60) {
    return t("errors.wait_seconds", { count: seconds });
  }
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) {
    return t("errors.wait_minutes", { count: minutes });
  }
  const hours = Math.ceil(minutes / 60);
  return t("errors.wait_hours", { count: hours });
}

function formatCodexUsageLimitMessage(t: TranslateFn, params: MessageParams = {}): string {
  const retryAt = params.retry_at ?? params.waitTime;
  const retrySuffix =
    typeof retryAt === "string" && retryAt ? t("errors.codex_usage_limit_retry", { retryAt }) : "";
  return t("errors.codex_usage_limit", { retrySuffix });
}

function translateErrorCode(code: string, t: TranslateFn, params: MessageParams = {}): string | null {
  const normalized = code.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (normalized === "codex_usage_limit" || normalized === "codex_usage_low") {
    return formatCodexUsageLimitMessage(t, params);
  }
  if (normalized === "content_policy") {
    const reason = typeof params.reason === "string" ? params.reason.trim() : "";
    return t("errors.content_policy", { reason: reason ? ` ${reason}` : "" });
  }
  const key = `errors.${normalized}`;
  if (!i18n.exists(key)) {
    return null;
  }
  return t(key, params);
}

function parseCodeMessage(raw: string): { code: string; params: MessageParams } | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;

  const providerFailed = trimmed.match(/^([a-z_]+)_provider_failed:\s*(.+)$/i);
  if (providerFailed) {
    const inner = parseCodeMessage(providerFailed[2]);
    if (inner) return inner;
    return { code: "provider_failed", params: {} };
  }

  const providerConfig = trimmed.match(
    /^(real_generation_provider_not_configured|fixture_generation_disabled_in_production):\s*(.+)$/i,
  );
  if (providerConfig) {
    return {
      code: providerConfig[1].toLowerCase(),
      params: { provider: providerConfig[2] },
    };
  }

  const candidateReady = trimmed.match(/^candidate_ready:(\d+)$/i);
  if (candidateReady) {
    return { code: "candidate_ready", params: { index: Number(candidateReady[1]) } };
  }

  const candidateProgress = trimmed.match(/^candidate_progress:(\d+)\/(\d+)$/i);
  if (candidateProgress) {
    return {
      code: "candidate_progress",
      params: {
        completed: Number(candidateProgress[1]),
        total: Number(candidateProgress[2]),
      },
    };
  }

  const withParam = trimmed.match(/^([a-z_]+):([a-z_]+)=(.+)$/i);
  if (withParam) {
    return {
      code: withParam[1].toLowerCase(),
      params: { [withParam[2].toLowerCase()]: withParam[3] },
    };
  }

  if (/^[a-z][a-z0-9_]*$/i.test(trimmed)) {
    return { code: trimmed.toLowerCase(), params: {} };
  }

  return null;
}

function resolveStructuredDetail(value: Record<string, unknown>, t: TranslateFn): string | null {
  if (value.code === "codex_usage_low" || typeof value.remaining_percent === "number") {
    const waitSeconds = typeof value.reset_wait_seconds === "number" ? value.reset_wait_seconds : null;
    return translateErrorCode("codex_usage_low", t, {
      waitTime: waitSeconds == null ? "" : formatWaitTime(waitSeconds, t),
    });
  }

  if (typeof value.code === "string") {
    const translated = translateErrorCode(value.code, t);
    if (translated) return translated;
  }

  if (typeof value.message === "string") {
    return resolveUserMessage(value.message, t);
  }

  return null;
}

const LEGACY_MESSAGE_MAP: Record<string, string> = {
  排队中: "queued",
  准备素材: "preparing_references",
  生成中: "generating",
  保存结果: "saving_outputs",
  生成完成: "generation_complete",
  生成失败: "generation_failed",
  已取消: "cancelled",
  "已选择候选，已停止剩余生成": "candidate_accepted_stopped",
  "服务重启，任务已中断": "service_restarted_interrupted",
  "当前生成任务已失效，请重新生成。": "job_expired",
};

function resolveLegacyMessage(raw: string, t: TranslateFn): string | null {
  const mapped = LEGACY_MESSAGE_MAP[raw];
  if (mapped) {
    return translateErrorCode(mapped, t);
  }

  const legacyProgress = raw.match(/^结果\s*(\d+)\/(\d+)\s*已完成$/);
  if (legacyProgress) {
    return translateErrorCode("candidate_progress", t, {
      completed: Number(legacyProgress[1]),
      total: Number(legacyProgress[2]),
    });
  }

  const legacyReady = raw.match(/^结果\s*(\d+)\s*已完成$/);
  if (legacyReady) {
    return translateErrorCode("candidate_ready", t, { index: Number(legacyReady[1]) });
  }

  return null;
}

function resolvePatternMessage(raw: string, t: TranslateFn): string | null {
  if (/codex_usage_low|remaining_percent|reset_wait_seconds/i.test(raw)) {
    const waitMatch = raw.match(/reset_wait_seconds=(\d+)/i);
    const waitSeconds = waitMatch ? Number(waitMatch[1]) : null;
    return translateErrorCode("codex_usage_low", t, {
      waitTime: waitSeconds == null ? "" : formatWaitTime(waitSeconds, t),
    });
  }

  if (
    /did not return an image/i.test(raw) ||
    /manifest/i.test(raw) ||
    /did not use the image generation tool/i.test(raw) ||
    /FileNotFoundError:.*manifest\.json/i.test(raw)
  ) {
    return translateErrorCode("provider_no_image", t);
  }

  if (/Codex CLI timed out/i.test(raw)) {
    return translateErrorCode("detail_analysis_request_timeout", t);
  }

  if (/load failed|failed to fetch|network/i.test(raw)) {
    return translateErrorCode("network_error", t);
  }

  const candidateReadyMatch = raw.match(/(?:candidate|结果)\s*(\d+)(?:\s*\/\s*\d+)?\s*(?:ready|已完成)/i);
  if (candidateReadyMatch) {
    return translateErrorCode("candidate_ready", t, { index: Number(candidateReadyMatch[1]) });
  }

  const resultProgressMatch = raw.match(/结果\s*(\d+)(?:\/(\d+))?\s*已完成/);
  if (resultProgressMatch) {
    if (resultProgressMatch[2]) {
      return translateErrorCode("candidate_progress", t, {
        completed: Number(resultProgressMatch[1]),
        total: Number(resultProgressMatch[2]),
      });
    }
    return translateErrorCode("candidate_ready", t, { index: Number(resultProgressMatch[1]) });
  }

  return null;
}

export function resolveUserMessage(value: unknown, t: TranslateFn = i18n.t.bind(i18n)): string {
  if (value && typeof value === "object") {
    const structured = resolveStructuredDetail(value as Record<string, unknown>, t);
    if (structured) return structured;
    if ("message" in value && typeof value.message === "string") {
      return resolveUserMessage(value.message, t);
    }
  }

  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return "";

  const parsed = parseCodeMessage(raw);
  if (parsed) {
    const translated = translateErrorCode(parsed.code, t, parsed.params);
    if (translated) return translated;
  }

  const legacyMessage = resolveLegacyMessage(raw, t);
  if (legacyMessage) return legacyMessage;

  const patternMessage = resolvePatternMessage(raw, t);
  if (patternMessage) return patternMessage;

  const normalized = raw.toLowerCase().replace(/[\s-]+/g, "_");
  const statusTranslated = translateErrorCode(normalized, t);
  if (statusTranslated) return statusTranslated;

  if (looksLikeLegacyMojibake(raw)) {
    return translateErrorCode("generation_status_update", t) ?? raw;
  }

  return raw
    .replace(/\bcodex\s+generating\b/gi, t("errors.generating"))
    .replace(/\bcodex(?:\s+cli|\s+bridge)?\b/gi, t("errors.generation_service"))
    .replace(/codex_/gi, "")
    .replace(/_/g, " ");
}

export function resolveGenerationStatusLabel(status: string, t: TranslateFn = i18n.t.bind(i18n)): string {
  const normalized = status.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (STATUS_LABEL_CODES.has(normalized)) {
    return translateErrorCode(normalized, t) ?? translateErrorCode("processing", t) ?? status;
  }
  return resolveUserMessage(status, t) || translateErrorCode("processing", t) || status;
}

export function resolveGenerationError(error: unknown, t: TranslateFn = i18n.t.bind(i18n)): string {
  if (error && typeof error === "object" && "status" in error && "detail" in error) {
    const apiError = error as { status: number; detail: unknown };
    if (typeof apiError.detail === "string") {
      if (apiError.detail === "detail_analysis_request_timeout") {
        return translateErrorCode("detail_analysis_request_timeout", t) ?? apiError.detail;
      }
      if (apiError.detail === "reference_adult_explicit") {
        return translateErrorCode("reference_adult_explicit", t) ?? apiError.detail;
      }
      if (apiError.detail === "reference_unusable") {
        return translateErrorCode("reference_unusable", t) ?? apiError.detail;
      }
      if (apiError.detail === "generation_rate_limited") {
        return translateErrorCode("generation_rate_limited", t) ?? apiError.detail;
      }
      if (apiError.detail === "generation_daily_quota_exceeded") {
        return translateErrorCode("generation_daily_quota_exceeded", t) ?? apiError.detail;
      }
      const resolved = resolveUserMessage(apiError.detail, t);
      if (resolved !== apiError.detail) return resolved;
      if (apiError.status >= 500) {
        return translateErrorCode("provider_failed", t) ?? resolved;
      }
      return resolved;
    }

    if (apiError.detail && typeof apiError.detail === "object" && "code" in apiError.detail) {
      const detail = apiError.detail as { code?: unknown; reset_at?: unknown };
      if (detail.code === "generation_daily_quota_exceeded") {
        const resetAt = typeof detail.reset_at === "string" ? formatQuotaResetAt(detail.reset_at) : "";
        return translateErrorCode("generation_daily_quota_exceeded", t, { resetAt }) ?? "generation_daily_quota_exceeded";
      }
    }

    const detailMessage = resolveUserMessage(apiError.detail, t);
    if (detailMessage) return detailMessage;
    if (apiError.status >= 500) {
      return translateErrorCode("provider_failed", t) ?? `generation_request_failed:${apiError.status}`;
    }
    return translateErrorCode("generation_request_failed", t, { status: apiError.status }) ?? "generation_request_failed";
  }

  if (error instanceof Error) {
    const resolved = resolveUserMessage(error.message, t);
    if (resolved !== error.message) return resolved;
    return error.message;
  }

  return translateErrorCode("generation_request_failed", t, { status: 0 }) ?? "generation_request_failed";
}

function formatQuotaResetAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function looksLikeLegacyMojibake(raw: string) {
  return /[鐢鎺閻闁閹鑽燧]/.test(raw) || (/[€?]/.test(raw) && /[濆瀹垚]/.test(raw));
}
