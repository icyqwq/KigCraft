import type { JobEvent } from "../../api/client";

export type GenerationDurationSample = {
  durationMs: number;
  expectedOutputCount?: number;
  finishedAt: string;
  generationMode?: string;
  jobId: string;
};

export const GENERATION_DURATION_HISTORY_STORAGE_KEY = "kig-preview.generation-duration-history.v1";

const MAX_HISTORY_SAMPLES = 30;
const MIN_ETA_HISTORY_SAMPLES = 3;
const MIN_DURATION_MS = 10_000;
const MAX_DURATION_MS = 6 * 60 * 60 * 1000;
const ONE_IMAGE_BENCHMARK_DURATION_MS = 6 * 60_000;
const FOUR_IMAGE_BENCHMARK_DURATION_MS = 18 * 60_000;
const TURNAROUND_BENCHMARK_DURATION_MS = 10 * 60_000;
const DETAIL_ANALYSIS_BENCHMARK_DURATION_MS = 90_000;

export function readGenerationDurationHistory(): GenerationDurationSample[] {
  if (typeof window === "undefined") return [];

  try {
    const parsed = JSON.parse(window.localStorage.getItem(GENERATION_DURATION_HISTORY_STORAGE_KEY) ?? "[]") as unknown;
    if (!Array.isArray(parsed)) return [];

    return parsed.filter(isDurationSample).slice(-MAX_HISTORY_SAMPLES);
  } catch {
    return [];
  }
}

export function writeGenerationDurationHistory(samples: GenerationDurationSample[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(GENERATION_DURATION_HISTORY_STORAGE_KEY, JSON.stringify(samples.slice(-MAX_HISTORY_SAMPLES)));
}

export function appendGenerationDurationSample(
  samples: GenerationDurationSample[],
  sample: GenerationDurationSample,
) {
  if (!isUsefulDuration(sample.durationMs)) return samples;

  const withoutDuplicate = samples.filter((existingSample) => existingSample.jobId !== sample.jobId);
  return [...withoutDuplicate, sample].slice(-MAX_HISTORY_SAMPLES);
}

export function estimateGenerationDurationMs(
  samples: GenerationDurationSample[],
  context: { expectedOutputCount?: number; generationMode?: string } = {},
) {
  const durations = samples
    .filter((sample) => matchesExpectedOutputCount(sample, context.expectedOutputCount))
    .filter((sample) => matchesGenerationMode(sample, context.generationMode))
    .map((sample) => sample.durationMs)
    .filter(isUsefulDuration)
    .sort((left, right) => left - right);

  if (durations.length < MIN_ETA_HISTORY_SAMPLES) return getBenchmarkDurationMs(context);

  const median = getMedian(durations);
  const deviations = durations.map((duration) => Math.abs(duration - median)).sort((left, right) => left - right);
  const mad = getMedian(deviations);
  const maxDeviation = mad > 0 ? 3 * 1.4826 * mad : Number.POSITIVE_INFINITY;
  const filtered = durations.filter((duration) => Math.abs(duration - median) <= maxDeviation);
  const robustDurations = filtered.length >= MIN_ETA_HISTORY_SAMPLES ? filtered : durations;
  const trimCount = robustDurations.length >= 8 ? Math.floor(robustDurations.length * 0.2) : 0;
  const trimmed =
    trimCount > 0 ? robustDurations.slice(trimCount, robustDurations.length - trimCount) : robustDurations;
  const total = trimmed.reduce((sum, duration) => sum + duration, 0);

  return Math.round(total / trimmed.length);
}

function matchesExpectedOutputCount(sample: GenerationDurationSample, expectedOutputCount: number | undefined) {
  if (expectedOutputCount === undefined) return true;
  if (sample.expectedOutputCount === undefined) return true;
  return sample.expectedOutputCount === expectedOutputCount;
}

function matchesGenerationMode(sample: GenerationDurationSample, generationMode: string | undefined) {
  if (generationMode === undefined) return true;
  if (sample.generationMode === undefined) return true;
  return sample.generationMode === generationMode;
}

function getBenchmarkDurationMs(context: { expectedOutputCount?: number; generationMode?: string }) {
  if (context.generationMode === "detail_analysis") return DETAIL_ANALYSIS_BENCHMARK_DURATION_MS;
  if (context.generationMode === "turnaround") return TURNAROUND_BENCHMARK_DURATION_MS;
  if (context.expectedOutputCount === 4) return FOUR_IMAGE_BENCHMARK_DURATION_MS;
  return ONE_IMAGE_BENCHMARK_DURATION_MS;
}

export function estimateGenerationQueueWaitMs(queuePosition: number | null | undefined, estimatedDurationMs: number | null) {
  if (!queuePosition || queuePosition <= 1 || estimatedDurationMs === null) return 0;
  return (queuePosition - 1) * estimatedDurationMs;
}

export function formatEtaDuration(ms: number, language = "zh-CN") {
  const minutes = Math.max(1, Math.ceil(ms / 60_000));
  return formatMinutes(minutes, language);
}

export function getGenerationStartTimeMs(events: JobEvent[], fallbackMs: number) {
  const firstEventTime = events.map(getEventTimeMs).find((eventTime) => eventTime !== null);
  return firstEventTime ?? fallbackMs;
}

export function getGenerationFinishTimeMs(events: JobEvent[], fallbackMs: number) {
  const terminalEventTime = [...events]
    .reverse()
    .find((event) => ["accepted", "cancelled", "failed", "succeeded"].includes(event.type))?.created_at;
  const parsedTerminalTime = terminalEventTime ? Date.parse(terminalEventTime) : Number.NaN;
  if (Number.isFinite(parsedTerminalTime)) return parsedTerminalTime;

  const lastEventTime = [...events].reverse().map(getEventTimeMs).find((eventTime) => eventTime !== null);
  return lastEventTime ?? fallbackMs;
}

export function formatDuration(ms: number, language = "zh-CN") {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  if (totalSeconds < 60) return formatSeconds(totalSeconds, language);

  const totalMinutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (totalMinutes < 60) {
    return seconds === 0
      ? formatMinutes(totalMinutes, language)
      : formatMinutesAndSeconds(totalMinutes, seconds, language);
  }

  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes === 0 ? formatHours(hours, language) : formatHoursAndMinutes(hours, minutes, language);
}

function getDurationLocale(language: string) {
  const normalized = language.toLowerCase();
  if (normalized.startsWith("en")) return "en";
  if (normalized.startsWith("ja")) return "ja";
  return "zh-CN";
}

function formatSeconds(seconds: number, language: string) {
  const locale = getDurationLocale(language);
  if (locale === "en") return `${seconds} sec`;
  if (locale === "ja") return `${seconds} 秒`;
  return `${seconds} 秒`;
}

function formatMinutes(minutes: number, language: string) {
  const locale = getDurationLocale(language);
  if (locale === "en") return minutes === 1 ? "1 min" : `${minutes} min`;
  if (locale === "ja") return `${minutes} 分`;
  return `${minutes} 分钟`;
}

function formatMinutesAndSeconds(minutes: number, seconds: number, language: string) {
  const locale = getDurationLocale(language);
  if (locale === "en") return `${minutes} min ${seconds} sec`;
  if (locale === "ja") return `${minutes} 分 ${seconds} 秒`;
  return `${minutes} 分 ${seconds} 秒`;
}

function formatHours(hours: number, language: string) {
  const locale = getDurationLocale(language);
  if (locale === "en") return hours === 1 ? "1 hr" : `${hours} hr`;
  if (locale === "ja") return `${hours} 時間`;
  return `${hours} 小时`;
}

function formatHoursAndMinutes(hours: number, minutes: number, language: string) {
  const locale = getDurationLocale(language);
  if (locale === "en") return `${hours} hr ${minutes} min`;
  if (locale === "ja") return `${hours} 時間 ${minutes} 分`;
  return `${hours} 小时 ${minutes} 分钟`;
}

function getEventTimeMs(event: JobEvent) {
  const eventTime = Date.parse(event.created_at);
  return Number.isFinite(eventTime) ? eventTime : null;
}

function getMedian(values: number[]) {
  const middle = Math.floor(values.length / 2);
  if (values.length % 2 === 1) return values[middle];
  return (values[middle - 1] + values[middle]) / 2;
}

function isUsefulDuration(durationMs: number) {
  return Number.isFinite(durationMs) && durationMs >= MIN_DURATION_MS && durationMs <= MAX_DURATION_MS;
}

function isDurationSample(value: unknown): value is GenerationDurationSample {
  if (!value || typeof value !== "object") return false;
  const sample = value as Partial<GenerationDurationSample>;
  return (
    typeof sample.durationMs === "number" &&
    typeof sample.finishedAt === "string" &&
    typeof sample.jobId === "string" &&
    isUsefulDuration(sample.durationMs)
  );
}
