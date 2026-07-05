import { Badge, Box, Button, CircularProgress, Group, Paper, Stack, Text, Title } from "../../ui/mui";
import { IconPhoto, IconRefresh } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { GenerationJob, JobEvent } from "../../api/client";
import { formatGenerationMessage, formatGenerationStatusLabel, shouldShowGenerationEvent } from "./generationDisplay";
import {
  appendGenerationDurationSample,
  estimateGenerationDurationMs,
  estimateGenerationQueueWaitMs,
  formatDuration,
  formatEtaDuration,
  getGenerationFinishTimeMs,
  getGenerationStartTimeMs,
  readGenerationDurationHistory,
  writeGenerationDurationHistory,
} from "./generationTiming";

export type GenerationReviewProps = {
  job: GenerationJob | null;
  events: JobEvent[];
  selectedCandidateIndex: number | null;
  onSelectCandidate: (index: number) => void;
  onRefresh: () => void;
};

type TimelineItem = {
  key: string;
  title: string;
  createdAt: string;
};

export function GenerationReview({ job, events, onRefresh }: GenerationReviewProps) {
  const { i18n, t } = useTranslation();
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [durationHistory, setDurationHistory] = useState(readGenerationDurationHistory);
  const localStartMsByJobIdRef = useRef(new Map<string, number>());
  const recordedDurationJobIdsRef = useRef(new Set<string>());
  const estimatedDurationMs = useMemo(
    () =>
      estimateGenerationDurationMs(durationHistory, {
        expectedOutputCount: job?.expected_output_count,
        generationMode: job?.generation_mode,
      }),
    [durationHistory, job?.expected_output_count, job?.generation_mode],
  );
  const hasActiveGeneration = job ? !isTerminalStatus(job.status) : false;
  const fallbackStartMs = getFallbackStartMs(job?.id, nowMs, localStartMsByJobIdRef.current);
  const jobStartMs = job ? getGenerationStartTimeMs(events, fallbackStartMs) : nowMs;
  const jobFinishMs = job ? (hasActiveGeneration ? nowMs : getGenerationFinishTimeMs(events, nowMs)) : nowMs;
  const elapsedMs = job ? Math.max(0, jobFinishMs - jobStartMs) : 0;
  const queueWaitMs = job?.status === "queued" ? estimateGenerationQueueWaitMs(job.queue_position, estimatedDurationMs) : 0;
  const etaMs =
    job && hasActiveGeneration && estimatedDurationMs !== null
      ? Math.max(0, estimatedDurationMs + queueWaitMs - elapsedMs)
      : null;

  useEffect(() => {
    if (!job || isTerminalStatus(job.status) || typeof window === "undefined") return;

    const intervalId = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);

    return () => window.clearInterval(intervalId);
  }, [job?.id, job?.status, job]);

  useEffect(() => {
    if (!job || !["accepted", "succeeded"].includes(job.status)) return;
    if (recordedDurationJobIdsRef.current.has(job.id)) return;

    const finishMs = getGenerationFinishTimeMs(events, nowMs);
    const durationMs = finishMs - getGenerationStartTimeMs(events, fallbackStartMs);
    const nextHistory = appendGenerationDurationSample(durationHistory, {
      durationMs,
      expectedOutputCount: job.expected_output_count,
      finishedAt: new Date(finishMs).toISOString(),
      generationMode: job.generation_mode,
      jobId: job.id,
    });

    recordedDurationJobIdsRef.current.add(job.id);
    if (nextHistory !== durationHistory) {
      setDurationHistory(nextHistory);
      writeGenerationDurationHistory(nextHistory);
    }
  }, [durationHistory, events, fallbackStartMs, job, nowMs]);

  if (!job) {
    return (
      <Paper
        className="grunge-card"
        p={{ base: 2, md: 2.25 }}
        radius="md"
        withBorder
        style={{ borderColor: "var(--kb-line)" }}
      >
        <Stack gap={1}>
          <Group gap={1}>
            <IconPhoto color="var(--kb-dirty-yellow)" size={20} />
            <Title c="white" order={2} size="h3">
              {t("generation.waitingTitle")}
            </Title>
          </Group>
          <Text c="dimmed" size="sm">
            {t("generation.waitingDescription")}
          </Text>
        </Stack>
      </Paper>
    );
  }

  const timelineItems = getTimelineItems(job, events);
  const currentTimelineItem = timelineItems[timelineItems.length - 1];
  const statusLabel = formatGenerationStatusLabel(job.status);
  const phaseLabel = formatGenerationMessage(job.phase_label || job.status) || statusLabel;
  const currentDetailText = currentTimelineItem
    ? formatTimelineDetail(currentTimelineItem) || t("generation.waitingForProgress")
    : t("generation.waitingForProgress");

  return (
    <Paper
      className="grunge-card"
      p={{ base: 2, md: 2.25 }}
      radius="md"
      withBorder
      style={{ borderColor: "var(--kb-line)" }}
    >
      <Stack gap={1.25}>
        <Group align="flex-start" justify="space-between" gap={1.5}>
          <Box>
            <Group gap={1}>
              <Title c="white" order={2} size="h3">
                {t("generation.statusTitle")}
              </Title>
              <Badge color={getStatusColor(job.status)} data-testid="generation-status" radius="sm" variant="light">
                {statusLabel}
              </Badge>
            </Group>
            <Text c="dimmed" data-testid="generation-job-id" mt={0.5} size="sm">
              {t("generation.jobLabel", { id: job.id.slice(0, 8) })}
            </Text>
          </Box>
          <Button color="cyan" leftSection={<IconRefresh size={16} />} onClick={onRefresh} size="xs" variant="light">
            {t("common.refresh")}
          </Button>
        </Group>

        <Stack gap={0.75}>
          <Group align="center" gap={1.25} wrap="nowrap">
            <Box
              style={{
                alignItems: "center",
                display: "inline-flex",
                height: 42,
                justifyContent: "center",
                minWidth: 42,
                position: "relative",
                width: 42,
              }}
            >
              <CircularProgress
                data-testid="generation-spinner"
                size={40}
                thickness={4.5}
                style={{ color: "var(--kb-dirty-yellow)" }}
              />
            </Box>
            <Box style={{ minWidth: 0 }}>
              <Text c="cyan.2" fw={700} size="sm">
                {phaseLabel}
              </Text>
            </Box>
          </Group>
          <Group align="center" justify="space-between" gap={1} wrap="wrap">
            <Text c="dimmed" data-testid="generation-current-detail" size="xs">
              {currentDetailText}
            </Text>
            <Group gap={0.75} justify="flex-end" wrap="wrap">
              <Badge color="gray" data-testid="generation-elapsed" radius="sm" variant="light">
                {t("generation.elapsed", { duration: formatDuration(elapsedMs, i18n.language) })}
              </Badge>
              {etaMs !== null && (
                <Badge color="cyan" data-testid="generation-eta" radius="sm" variant="light">
                  {t("generation.eta", { duration: formatEtaDuration(etaMs, i18n.language) })}
                </Badge>
              )}
              {job.status === "queued" && job.queue_position !== null && (
                <Badge color="cyan" data-testid="generation-queue-position" radius="sm" variant="light">
                  {t("generation.queuePosition", { position: job.queue_position })}
                </Badge>
              )}
            </Group>
          </Group>
        </Stack>
      </Stack>
    </Paper>
  );
}

function formatTimelineDetail(item: TimelineItem) {
  return `${formatGenerationMessage(item.title)}${item.createdAt ? ` · ${formatEventTime(item.createdAt)}` : ""}`;
}

function getTimelineItems(job: GenerationJob, events: JobEvent[]): TimelineItem[] {
  const visibleEvents = events.filter((event) => shouldShowGenerationEvent(event.type));

  if (visibleEvents.length === 0) {
    return [
      {
        key: "current-phase",
        title: job.phase_label || job.status,
        createdAt: "",
      },
    ];
  }

  return visibleEvents.map((event) => ({
    key: `${event.sequence}-${event.type}`,
    title: event.message || event.type,
    createdAt: event.created_at,
  }));
}

function getFallbackStartMs(jobId: string | undefined, nowMs: number, startMsByJobId: Map<string, number>) {
  if (!jobId) return nowMs;
  const existingStartMs = startMsByJobId.get(jobId);
  if (existingStartMs !== undefined) return existingStartMs;
  startMsByJobId.set(jobId, nowMs);
  return nowMs;
}

function getStatusColor(status: string) {
  if (status === "succeeded" || status === "accepted") return "green";
  if (status === "failed") return "red";
  if (status === "cancelled") return "gray";
  if (status === "queued") return "blue";
  return "cyan";
}

function isTerminalStatus(status: string) {
  return ["succeeded", "failed", "cancelled", "accepted"].includes(status);
}

function formatEventTime(value: string) {
  if (!value) return "";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
