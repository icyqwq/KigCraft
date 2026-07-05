import {
  Alert,
  AppShell,
  Badge,
  Box,
  Button,
  Container,
  Divider,
  Group,
  NumberInput,
  Paper,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
  Image,
} from "../ui/mui";
import { IconArrowLeft, IconChevronRight, IconDeviceFloppy, IconLock, IconLogout, IconPhoto, IconRefresh, IconShieldCheck } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  getAuditGenerationJobs,
  getAuditSession,
  getAuditSummary,
  loginAudit,
  logoutAudit,
  updateQuotaPolicy,
  type AuditSummary,
  type AuditGenerationJob,
  type QuotaPolicy,
} from "../api/client";
import { AuditOverviewCharts } from "./AuditOverviewCharts";

const defaultPolicy: QuotaPolicy = {
  window_hours: 5,
  normal_window_limit: 8,
  premium_unlimited: true,
  parallel_generation_limit: 8,
};

type TokenUsageSummary = {
  cached_input_tokens?: number | null;
  input_tokens?: number | null;
  jobs_with_usage?: number | null;
  output_tokens?: number | null;
  reasoning_output_tokens?: number | null;
  total_tokens?: number | null;
};

type ImageUsageSummary = {
  cached_input_tokens_per_image?: number | null;
  generated_images?: number | null;
  images_with_token_usage?: number | null;
  input_tokens_per_image?: number | null;
  jobs_with_outputs?: number | null;
  output_tokens_per_image?: number | null;
  reasoning_output_tokens_per_image?: number | null;
  total_tokens_per_image?: number | null;
};

function toNumberInputValue(value: string | number, fallback: number) {
  if (typeof value === "number") return value;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatPercent(value?: number) {
  return `${((value ?? 0) * 100).toFixed(1)}%`;
}

function formatInteger(value?: number | null) {
  return (value ?? 0).toLocaleString("en-US");
}

function formatDecimal(value?: number | null) {
  return (value ?? 0).toLocaleString("en-US", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 0,
  });
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
  });
}

function formatGenerationMode(value: string) {
  if (value === "front_design") return "正视图";
  if (value === "front_revision") return "重生成正视图";
  if (value === "turnaround") return "四视图";
  return value;
}

function statusColor(status: string) {
  if (status === "succeeded" || status === "accepted") return "teal";
  if (status === "failed" || status === "cancelled") return "red";
  if (status === "queued") return "yellow";
  return "cyan";
}

function formatAuditGenerationMode(value: string) {
  if (value === "front_design") return "正视图";
  if (value === "front_revision") return "重生成正视图";
  if (value === "turnaround") return "四视图";
  return value;
}

function formatAuditJobStatus(status: string) {
  if (status === "succeeded" || status === "accepted") return "succeeded";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  if (status === "queued") return "queued";
  return "running";
}

function formatAuditJobTitle(job: AuditGenerationJob) {
  if (job.status === "succeeded" || job.status === "accepted" || (job.outputs.length > 0 && job.progress >= 100)) {
    return "生成完成";
  }
  if (job.status === "failed") return "生成失败";
  if (job.status === "cancelled") return "已取消";
  if (job.status === "queued") return "排队中";
  return "生成中";
}

function getTokenUsage(summary?: AuditSummary | null): TokenUsageSummary {
  return ((summary as (AuditSummary & { token_usage?: TokenUsageSummary }) | undefined)?.token_usage ?? {}) as TokenUsageSummary;
}

function getImageUsage(summary?: AuditSummary | null): ImageUsageSummary {
  return ((summary as (AuditSummary & { image_usage?: ImageUsageSummary }) | undefined)?.image_usage ?? {}) as ImageUsageSummary;
}

function loginErrorMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 429) {
    return "重试次数过多，请稍后再试。";
  }
  if (error instanceof ApiError && error.status === 401) {
    return "密码错误。";
  }
  return "登录失败，请重试。";
}

function MetricBlock({
  label,
  value,
  hint,
  testId,
}: {
  label: string;
  value: string | number;
  hint?: string;
  testId?: string;
}) {
  return (
    <Paper
      className="audit-metric-card"
      data-testid={testId}
      p="md"
      radius={0}
    >
      <Text c="var(--kb-concrete-grey)" fw={700} size="sm">
        {label}
      </Text>
      <Text c="var(--kb-ink)" fw={900} mt={6} size="xl">
        {value}
      </Text>
      {hint ? (
        <Text c="var(--kb-concrete-grey)" mt={4} size="xs">
          {hint}
        </Text>
      ) : null}
    </Paper>
  );
}

function GenerationImageGrid({
  emptyText,
  images,
  testIdPrefix,
}: {
  emptyText: string;
  images: Array<{ image_url?: string | null; label: string; title?: string | null }>;
  testIdPrefix: string;
}) {
  const visibleImages = images.filter((image) => image.image_url);
  if (!visibleImages.length) {
    return (
      <Text c="var(--kb-concrete-grey)" size="sm">
        {emptyText}
      </Text>
    );
  }

  return (
    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
      {visibleImages.map((image, index) => (
        <Paper key={`${image.image_url}-${index}`} className="audit-image-card" p="xs" radius={0}>
          <Stack gap="xs">
            <Image
              alt={image.label}
              data-testid={`${testIdPrefix}-${index}`}
              fit="contain"
              h={180}
              src={image.image_url ?? undefined}
              style={{
                background: "#fff",
                border: "2px solid var(--kb-line)",
              }}
              w="100%"
            />
            <Text c="var(--kb-ink)" fw={700} size="xs">
              {image.label}
            </Text>
            {image.title ? (
              <Text c="var(--kb-concrete-grey)" size="xs">
                {image.title}
              </Text>
            ) : null}
          </Stack>
        </Paper>
      ))}
    </SimpleGrid>
  );
}

function GenerationJobRecords({
  jobs,
  loading,
  selectedJob,
  onRefresh,
  onSelect,
}: {
  jobs: AuditGenerationJob[];
  loading: boolean;
  selectedJob: AuditGenerationJob | null;
  onRefresh: () => void;
  onSelect: (jobId: string) => void;
}) {
  return (
    <Paper className="grunge-card" data-testid="audit-generation-records" p={{ base: "md", md: "lg" }} radius={0}>
      <Stack gap="md">
        <Group justify="space-between">
          <Box>
            <Title c="var(--kb-ink)" order={2} size="h3">
              生图记录
            </Title>
            <Text c="var(--kb-concrete-grey)" size="sm">
              查看当前后端进程内的生图任务、提交参考图和输出图片。列表支持滚动，共 {jobs.length} 条。
            </Text>
          </Box>
          <Button leftSection={<IconRefresh size={16} />} loading={loading} onClick={onRefresh} variant="subtle">
            刷新
          </Button>
        </Group>

        {loading && !jobs.length ? (
          <Text c="var(--kb-concrete-grey)">正在加载生图记录...</Text>
        ) : !jobs.length ? (
          <Alert color="yellow" radius="sm" variant="light">
            暂无生图任务记录。
          </Alert>
        ) : (
          <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
            <Box className="audit-generation-job-list">
              <Stack gap="sm">
              {jobs.map((job) => {
                const selected = selectedJob?.id === job.id;
                return (
                  <Paper
                    key={job.id}
                    component="button"
                    data-testid={`audit-generation-job-${job.id}`}
                    onClick={() => onSelect(job.id)}
                    p="sm"
                    radius={0}
                    style={{
                      background: selected ? "var(--kb-old-paper-2)" : "var(--kb-panel)",
                      border: "2px solid var(--kb-line)",
                      boxShadow: selected ? "var(--kb-hard-shadow-sm)" : "none",
                      cursor: "pointer",
                      textAlign: "left",
                      width: "100%",
                    }}
                  >
                    <Group gap="sm" justify="space-between" wrap="nowrap">
                      <Stack gap={0.25}>
                        <Group gap="xs" wrap="wrap">
                          <Badge color={statusColor(job.status)} variant="light">
                            {formatAuditJobStatus(job.status)}
                          </Badge>
                          <Badge color="cyan" variant="light">
                            {formatAuditGenerationMode(job.generation_mode)}
                          </Badge>
                        </Group>
                        <Text c="var(--kb-ink)" fw={800} size="sm">
                          {formatAuditJobTitle(job)}
                        </Text>
                        <Text c="var(--kb-concrete-grey)" size="xs">
                          {formatDateTime(job.updated_at ?? job.created_at)} · 输出 {job.outputs.length} 张 · 参考 {job.references.length} 张
                        </Text>
                      </Stack>
                      <IconChevronRight size={20} />
                    </Group>
                  </Paper>
                );
              })}
              </Stack>
            </Box>

            <Paper
              className="audit-generation-job-detail"
              p="md"
              radius={0}
              style={{ border: "2px solid var(--kb-line)" }}
            >
              {selectedJob ? (
                <Stack gap="md" data-testid="audit-generation-job-detail">
                  <Group gap="sm" justify="space-between">
                    <Box>
                      <Title c="var(--kb-ink)" order={3} size="h4">
                        任务详情
                      </Title>
                      <Text c="var(--kb-concrete-grey)" size="xs">
                        {selectedJob.id}
                      </Text>
                    </Box>
                    <Badge color={statusColor(selectedJob.status)} variant="light">
                      {selectedJob.status}
                    </Badge>
                  </Group>

                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
                    <MetricBlock label="模式" value={formatAuditGenerationMode(selectedJob.generation_mode)} />
                    <MetricBlock label="Provider" value={selectedJob.provider} />
                    <MetricBlock label="Session" value={selectedJob.character_session_id} />
                    <MetricBlock label="Token" value={formatInteger(selectedJob.token_usage?.total_tokens)} />
                  </SimpleGrid>

                  <Divider color="var(--kb-line)" />
                  <Box>
                    <Text c="var(--kb-ink)" fw={900}>
                      提交文字
                    </Text>
                    <Text c="var(--kb-concrete-grey)" mt={4} size="sm">
                      {selectedJob.user_notes || selectedJob.user_requirements || "无文字 prompt"}
                    </Text>
                  </Box>

                  <Box>
                    <Group gap="xs" mb="sm">
                      <IconPhoto size={18} />
                      <Text c="var(--kb-ink)" fw={900}>
                        提交参考图
                      </Text>
                    </Group>
                    <GenerationImageGrid
                      emptyText="无参考图记录。"
                      images={selectedJob.references.map((reference) => ({
                        image_url: reference.image_url,
                        label: reference.kind ? `${reference.kind} 参考` : "参考图",
                        title: reference.description || reference.reference_key,
                      }))}
                      testIdPrefix="audit-reference-image"
                    />
                  </Box>

                  <Box>
                    <Group gap="xs" mb="sm">
                      <IconPhoto size={18} />
                      <Text c="var(--kb-ink)" fw={900}>
                        输出图片
                      </Text>
                    </Group>
                    <GenerationImageGrid
                      emptyText="暂无输出图片。"
                      images={selectedJob.outputs.map((output) => ({
                        image_url: output.image_url,
                        label: `输出 ${output.index}`,
                        title: `${output.width} x ${output.height}`,
                      }))}
                      testIdPrefix="audit-output-image"
                    />
                  </Box>
                </Stack>
              ) : (
                <Text c="var(--kb-concrete-grey)">选择一条记录查看详情。</Text>
              )}
            </Paper>
          </SimpleGrid>
        )}
      </Stack>
    </Paper>
  );
}

function AuditLoginPanel({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [password, setPassword] = useState("");
  const loginMutation = useMutation({
    mutationFn: loginAudit,
    onSuccess: () => {
      setPassword("");
      onAuthenticated();
    },
  });

  return (
    <Container py={{ base: "lg", md: "xl" }} size="sm">
      <Paper
        className="grunge-card"
        p={{ base: "lg", md: "xl" }}
        radius={0}
      >
        <Stack gap="md">
          <Group gap="sm" wrap="nowrap">
            <Badge color="cyan" leftSection={<IconLock size={14} />} radius="sm" variant="light">
              Admin
            </Badge>
            <Box>
              <Title c="var(--kb-ink)" order={1} size="h3">
                审计面板
              </Title>
              <Text c="var(--kb-concrete-grey)" size="sm">
                请输入管理密码查看生成服务统计和配额设置。
              </Text>
            </Box>
          </Group>

          <TextInput
            autoComplete="current-password"
            label="访问密码"
            onChange={(event: React.ChangeEvent<HTMLInputElement>) => setPassword(event.currentTarget.value)}
            onKeyDown={(event: React.KeyboardEvent<HTMLInputElement>) => {
              if (event.key === "Enter" && password.trim()) {
                loginMutation.mutate(password);
              }
            }}
            type="password"
            value={password}
          />

          {loginMutation.isError ? (
            <Alert color="red" radius="sm" variant="light">
              {loginErrorMessage(loginMutation.error)}
            </Alert>
          ) : null}

          <Group justify="space-between">
            <Button component={Link} leftSection={<IconArrowLeft size={16} />} to="/" variant="subtle">
              返回
            </Button>
            <Button disabled={!password.trim()} loading={loginMutation.isPending} onClick={() => loginMutation.mutate(password)}>
              登录
            </Button>
          </Group>
        </Stack>
      </Paper>
    </Container>
  );
}

export function AdminAuditPage() {
  const queryClient = useQueryClient();
  const [draftPolicy, setDraftPolicy] = useState<QuotaPolicy>(defaultPolicy);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [selectedGenerationJobId, setSelectedGenerationJobId] = useState<string | null>(null);
  const sessionQuery = useQuery({
    queryKey: ["audit-session"],
    queryFn: getAuditSession,
    retry: false,
  });
  const isAuthenticated = Boolean(sessionQuery.data?.authenticated);
  const summaryQuery = useQuery({
    enabled: isAuthenticated,
    queryKey: ["audit-summary"],
    queryFn: getAuditSummary,
  });
  const summary = summaryQuery.data;
  const generationJobsQuery = useQuery({
    enabled: isAuthenticated,
    queryKey: ["audit-generation-jobs"],
    queryFn: getAuditGenerationJobs,
  });
  const generationJobs = generationJobsQuery.data ?? [];
  const selectedGenerationJob =
    generationJobs.find((job) => job.id === selectedGenerationJobId) ?? generationJobs[0] ?? null;
  const tokenUsage = getTokenUsage(summary);
  const imageUsage = getImageUsage(summary);
  const logoutMutation = useMutation({
    mutationFn: logoutAudit,
    onSuccess: () => {
      queryClient.setQueryData(["audit-session"], { authenticated: false });
      queryClient.removeQueries({ queryKey: ["audit-summary"] });
      queryClient.removeQueries({ queryKey: ["audit-generation-jobs"] });
      setSelectedGenerationJobId(null);
    },
  });
  const saveMutation = useMutation({
    mutationFn: updateQuotaPolicy,
    onSuccess: (policy) => {
      setDraftPolicy(policy);
      setSaveMessage("配额策略已保存。");
      queryClient.setQueryData(["audit-summary"], summary ? { ...summary, quota_policy: policy } : summary);
      void queryClient.invalidateQueries({ queryKey: ["audit-summary"] });
    },
  });

  useEffect(() => {
    if (summary?.quota_policy) {
      setDraftPolicy(summary.quota_policy);
    }
  }, [summary?.quota_policy]);

  return (
    <AppShell header={{ height: 72 }} padding={0}>
      <AppShell.Header
        bg="var(--kb-panel)"
        style={{
          borderBottom: "3px solid var(--kb-line)",
          boxShadow: "0 5px 0 var(--kb-shadow)",
        }}
      >
        <Container h="100%" size="xl">
          <Group h="100%" justify="space-between" wrap="nowrap">
            <Group gap="sm" wrap="nowrap">
              <Badge color="cyan" leftSection={<IconShieldCheck size={14} />} radius={0} variant="light">
                Admin
              </Badge>
              <Box>
                <Text c="var(--kb-ink)" fw={900}>
                  KigBuddy 审计
                </Text>
                <Text c="var(--kb-concrete-grey)" size="xs">
                  生成统计、队列状态、token 用量和配额策略
                </Text>
              </Box>
            </Group>
            <Button component={Link} leftSection={<IconArrowLeft size={16} />} to="/" variant="subtle">
              返回工作台
            </Button>
          </Group>
        </Container>
      </AppShell.Header>

      <AppShell.Main className="audit-page-surface" style={{ minHeight: "100vh" }}>
        {sessionQuery.isLoading ? (
          <Container py="xl" size="sm">
            <Text c="var(--kb-concrete-grey)">正在检查登录状态...</Text>
          </Container>
        ) : !isAuthenticated ? (
          <AuditLoginPanel
            onAuthenticated={() => {
              queryClient.setQueryData(["audit-session"], { authenticated: true });
              void queryClient.invalidateQueries({ queryKey: ["audit-summary"] });
              void queryClient.invalidateQueries({ queryKey: ["audit-generation-jobs"] });
            }}
          />
        ) : (
          <Container py={{ base: "md", md: "xl" }} size="xl">
            <Stack gap="lg">
              <Group align="flex-end" justify="space-between">
                <Box>
                  <Title c="var(--kb-ink)" order={1} size="h2">
                    审计面板
                  </Title>
                  <Text c="var(--kb-concrete-grey)" mt={6}>
                    查看生成调用、成功率、队列占用、图片产出、token 用量和访问配额。
                  </Text>
                </Box>
                <Group gap="sm">
                  <Badge color={summaryQuery.isFetching ? "yellow" : "teal"} radius={0} variant="light">
                    {summaryQuery.isFetching ? "刷新中" : "实时数据"}
                  </Badge>
                  <Button
                    leftSection={<IconLogout size={16} />}
                    loading={logoutMutation.isPending}
                    onClick={() => logoutMutation.mutate()}
                    variant="subtle"
                  >
                    退出
                  </Button>
                </Group>
              </Group>

              {summaryQuery.isError ? (
                <Alert color="red" radius="sm" variant="light">
                  审计数据加载失败，请确认后端服务和登录状态。
                </Alert>
              ) : null}

              <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="md">
                <MetricBlock
                  hint={`活跃用户 ${summary?.active_users ?? 0}`}
                  label="总用户"
                  testId="audit-total-users"
                  value={summary?.total_users ?? 0}
                />
                <MetricBlock label="总调用" testId="audit-total-calls" value={summary?.total_calls ?? 0} />
                <MetricBlock
                  hint={`失败率 ${formatPercent(summary?.failure_rate)}`}
                  label="成功率"
                  testId="audit-success-rate"
                  value={formatPercent(summary?.success_rate)}
                />
                <MetricBlock label="队列长度" testId="audit-queue-length" value={summary?.queue_length ?? 0} />
                <MetricBlock
                  hint={`${formatInteger(imageUsage.jobs_with_outputs)} 个任务有输出`}
                  label="生成图片"
                  testId="audit-generated-images"
                  value={formatInteger(imageUsage.generated_images)}
                />
                <MetricBlock
                  hint={`上限 ${draftPolicy.parallel_generation_limit}`}
                  label="并行占用"
                  testId="audit-parallel-slots"
                  value={summary?.parallel_slots_used ?? 0}
                />
              </SimpleGrid>

              {summary ? <AuditOverviewCharts jobs={generationJobs} summary={summary} /> : null}

              <Paper className="grunge-card" p={{ base: "md", md: "lg" }} radius={0}>
                <Stack gap="md">
                  <Group justify="space-between">
                    <Box>
                      <Title c="var(--kb-ink)" order={2} size="h3">
                        Token 用量
                      </Title>
                      <Text c="var(--kb-concrete-grey)" size="sm">
                        统计生成服务上报的输入、缓存输入、输出、推理 token，并按图片均摊。
                      </Text>
                    </Box>
                    <Badge color="cyan" variant="light">
                      {formatInteger(tokenUsage.jobs_with_usage)} 个任务
                    </Badge>
                  </Group>
                  <SimpleGrid cols={{ base: 1, sm: 2, lg: 5 }} spacing="md">
                    <MetricBlock
                      hint="输入、输出与推理 token 总和"
                      label="总 Token"
                      testId="audit-total-tokens"
                      value={formatInteger(tokenUsage.total_tokens)}
                    />
                    <MetricBlock
                      label="输入 Token"
                      testId="audit-input-tokens"
                      value={formatInteger(tokenUsage.input_tokens)}
                    />
                    <MetricBlock
                      label="缓存输入"
                      testId="audit-cached-input-tokens"
                      value={formatInteger(tokenUsage.cached_input_tokens)}
                    />
                    <MetricBlock
                      label="输出 Token"
                      testId="audit-output-tokens"
                      value={formatInteger(tokenUsage.output_tokens)}
                    />
                    <MetricBlock
                      label="推理 Token"
                      testId="audit-reasoning-output-tokens"
                      value={formatInteger(tokenUsage.reasoning_output_tokens)}
                    />
                  </SimpleGrid>
                  <Divider color="var(--kb-line)" />
                  <Group justify="space-between">
                    <Box>
                      <Title c="var(--kb-ink)" order={3} size="h4">
                        每张图 Token
                      </Title>
                      <Text c="var(--kb-concrete-grey)" size="sm">
                        按带 token 记录的输出图均摊；无 token 记录时显示 0。
                      </Text>
                    </Box>
                    <Badge color="teal" variant="light">
                      {formatInteger(imageUsage.images_with_token_usage)} 张图有 token
                    </Badge>
                  </Group>
                  <SimpleGrid cols={{ base: 1, sm: 2, lg: 5 }} spacing="md">
                    <MetricBlock
                      hint="总 token / 图片"
                      label="每图总 Token"
                      testId="audit-total-tokens-per-image"
                      value={formatDecimal(imageUsage.total_tokens_per_image)}
                    />
                    <MetricBlock
                      label="每图输入"
                      testId="audit-input-tokens-per-image"
                      value={formatDecimal(imageUsage.input_tokens_per_image)}
                    />
                    <MetricBlock
                      label="每图缓存输入"
                      testId="audit-cached-input-tokens-per-image"
                      value={formatDecimal(imageUsage.cached_input_tokens_per_image)}
                    />
                    <MetricBlock
                      label="每图输出"
                      testId="audit-output-tokens-per-image"
                      value={formatDecimal(imageUsage.output_tokens_per_image)}
                    />
                    <MetricBlock
                      label="每图推理"
                      testId="audit-reasoning-output-tokens-per-image"
                      value={formatDecimal(imageUsage.reasoning_output_tokens_per_image)}
                    />
                  </SimpleGrid>
                </Stack>
              </Paper>

              <GenerationJobRecords
                jobs={generationJobs}
                loading={generationJobsQuery.isFetching}
                selectedJob={selectedGenerationJob}
                onRefresh={() => void generationJobsQuery.refetch()}
                onSelect={setSelectedGenerationJobId}
              />

              <Paper className="grunge-card" p={{ base: "md", md: "lg" }} radius={0}>
                <Stack gap="md">
                  <Group justify="space-between">
                    <Box>
                      <Title c="var(--kb-ink)" order={2} size="h3">
                        配额策略
                      </Title>
                      <Text c="var(--kb-concrete-grey)" size="sm">
                        控制普通用户窗口配额和生成并发上限。
                      </Text>
                    </Box>
                    <Badge color={draftPolicy.premium_unlimited ? "green" : "gray"} variant="light">
                      {draftPolicy.premium_unlimited ? "高级用户无限制" : "高级用户同限额"}
                    </Badge>
                  </Group>

                  <Divider color="rgba(148, 163, 184, 0.16)" />

                  <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
                    <NumberInput
                      data-testid="quota-window-hours"
                      label="统计窗口（小时）"
                      onChange={(value) =>
                        setDraftPolicy((current) => ({
                          ...current,
                          window_hours: toNumberInputValue(value, current.window_hours),
                        }))
                      }
                      value={draftPolicy.window_hours}
                    />
                    <NumberInput
                      data-testid="quota-normal-window-limit"
                      label="普通用户窗口配额"
                      onChange={(value) =>
                        setDraftPolicy((current) => ({
                          ...current,
                          normal_window_limit: toNumberInputValue(value, current.normal_window_limit),
                        }))
                      }
                      value={draftPolicy.normal_window_limit}
                    />
                    <NumberInput
                      data-testid="quota-parallel-generation-limit"
                      label="并发生成上限"
                      onChange={(value) =>
                        setDraftPolicy((current) => ({
                          ...current,
                          parallel_generation_limit: toNumberInputValue(value, current.parallel_generation_limit),
                        }))
                      }
                      value={draftPolicy.parallel_generation_limit}
                    />
                  </SimpleGrid>

                  <Group justify="space-between">
                    <Switch
                      checked={draftPolicy.premium_unlimited}
                      label="高级用户不受普通配额限制"
                      onChange={(event) => {
                        const checked = event.currentTarget.checked;
                        setDraftPolicy((current) => ({
                          ...current,
                          premium_unlimited: checked,
                        }));
                      }}
                    />
                    <Button
                      leftSection={<IconDeviceFloppy size={16} />}
                      loading={saveMutation.isPending}
                      onClick={() => saveMutation.mutate(draftPolicy)}
                    >
                      保存策略
                    </Button>
                  </Group>

                  {saveMessage || saveMutation.isError ? (
                    <Alert color={saveMutation.isError ? "red" : "green"} radius="sm" variant="light">
                      {saveMutation.isError ? "策略保存失败，请重试。" : saveMessage}
                    </Alert>
                  ) : null}
                </Stack>
              </Paper>
            </Stack>
          </Container>
        )}
      </AppShell.Main>
    </AppShell>
  );
}
