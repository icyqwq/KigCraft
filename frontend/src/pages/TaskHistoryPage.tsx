import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Alert, Badge, Button, CircularProgress, Group, Paper, Stack, Text, Title } from "../ui/mui";
import { listGenerationJobs, type GenerationJob } from "../api/client";
import { useAuth } from "../features/auth/AuthProvider";
import { formatGenerationMessage } from "../features/generation/generationDisplay";

type TaskHistoryPageProps = {
  latestJob?: GenerationJob | null;
  onLoginRequired?: () => void;
  onOpenJob?: (job: GenerationJob) => void;
};

const terminalGenerationStatuses = new Set(["succeeded", "failed", "cancelled", "accepted"]);

function isTerminalHistoryStatus(status?: string) {
  return status ? terminalGenerationStatuses.has(status) : false;
}

function formatTaskTimestamp(job: GenerationJob) {
  const value = job.created_at ?? job.updated_at;
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString();
}

export function TaskHistoryPage({ latestJob, onLoginRequired, onOpenJob }: TaskHistoryPageProps) {
  const { t } = useTranslation();
  const { currentUser } = useAuth();
  const query = useQuery({
    queryKey: ["generation-jobs"],
    queryFn: () => listGenerationJobs(),
    enabled: Boolean(currentUser),
    refetchOnWindowFocus: false,
  });
  const jobs = query.data ?? [];
  const visibleJobs = latestJob ? [latestJob, ...jobs.filter((job) => job.id !== latestJob.id)] : jobs;

  if (!currentUser) {
    return (
      <Stack gap={3}>
        <Title order={1} size="h2">
          {t("auth.taskHistoryTitle")}
        </Title>
        <Paper data-testid="task-history-login-required" p={3} withBorder>
          <Stack gap={2}>
            <Text>{t("auth.taskHistoryLoginBody")}</Text>
            <Button color="cyan" data-testid="task-history-login-button" onClick={onLoginRequired} variant="filled">
              {t("auth.login")}
            </Button>
          </Stack>
        </Paper>
      </Stack>
    );
  }

  return (
    <Stack gap={3}>
      <Group align="center" justify="space-between">
        <Title order={1} size="h2">
          任务记录
        </Title>
        <Button loading={query.isFetching} onClick={() => void query.refetch()} variant="light">
          刷新
        </Button>
      </Group>

      {query.isPending && visibleJobs.length === 0 ? (
        <Paper data-testid="task-history-loading" p={3} withBorder>
          <Group align="center" gap={1.5} wrap="nowrap">
            <CircularProgress size={18} thickness={5} />
            <Stack gap={0.5}>
              <Text fw={900}>正在加载任务记录</Text>
              <Text c="dimmed" size="sm">
                请稍候，马上显示账号下的历史任务。
              </Text>
            </Stack>
          </Group>
        </Paper>
      ) : query.isError && visibleJobs.length === 0 ? (
        <Alert color="red" data-testid="task-history-error" variant="light">
          任务记录加载失败，请稍后重试。
        </Alert>
      ) : visibleJobs.length === 0 ? (
        <Paper data-testid="task-history-empty" p={3} withBorder>
          <Text c="dimmed">暂无任务记录</Text>
        </Paper>
      ) : (
        <Stack data-testid="task-history-list" gap={1.5}>
          {visibleJobs.map((job) => (
            <TaskHistoryItem job={job} key={job.id} onOpenJob={onOpenJob} />
          ))}
        </Stack>
      )}
    </Stack>
  );
}

function TaskHistoryItem({ job, onOpenJob }: { job: GenerationJob; onOpenJob?: (job: GenerationJob) => void }) {
  const timestamp = formatTaskTimestamp(job);
  return (
    <Paper data-testid={`task-history-item-${job.id}`} key={job.id} p={2} withBorder>
      <Stack direction={{ xs: "column", sm: "row" }} gap={1.5} justify="space-between">
        <Stack gap={0.5} miw={0}>
          <Text fw={900}>任务 {job.id.slice(0, 8)}</Text>
          <Text c="dimmed" size="sm">
            {formatGenerationMessage(job.phase_label || job.status)}
          </Text>
          {timestamp ? (
            <Text c="dimmed" size="xs">
              {timestamp}
            </Text>
          ) : null}
        </Stack>
        <Stack align="flex-end" direction={{ xs: "row", sm: "column" }} gap={1} justify="space-between">
          <Badge color={isTerminalHistoryStatus(job.status) ? "green" : "cyan"} variant="light">
            {formatGenerationMessage(job.status)}
          </Badge>
          {onOpenJob ? (
            <Button
              data-testid={`task-history-open-${job.id}`}
              onClick={() => onOpenJob(job)}
              size="small"
              variant="light"
            >
              打开任务
            </Button>
          ) : null}
        </Stack>
      </Stack>
    </Paper>
  );
}
