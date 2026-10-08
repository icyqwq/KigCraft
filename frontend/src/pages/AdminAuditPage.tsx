import { publicUrl } from "../ui/publicUrl";
import {
  ActionIcon,
  Alert,
  AppShell,
  Badge,
  Box,
  Button,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Group,
  Image,
  Menu,
  MenuItem,
  NumberInput,
  Paper,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea,
  Title,
} from "../ui/mui";
import {
  IconActivity,
  IconArrowLeft,
  IconChartBar,
  IconCheck,
  IconClock,
  IconDatabase,
  IconDeviceFloppy,
  IconDotsVertical,
  IconFileText,
  IconGauge,
  IconHistory,
  IconHome,
  IconPhoto,
  IconRefresh,
  IconShieldCheck,
  IconStack2,
  IconUsers,
  IconX,
} from "@tabler/icons-react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type UseMutationResult } from "@tanstack/react-query";
import type { ChangeEvent, Dispatch, KeyboardEvent, MouseEvent, ReactNode, SetStateAction } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { EChart } from "../components/charts/EChart";
import { buildDualTrendOption, buildRemainingQuotaOption, buildSparklineOption } from "../components/charts/adminChartOptions";
import {
  activateCodexProfile,
  adjustAdminUserDailyGenerationQuota,
  cancelCodexProfileDeviceAuth,
  deleteCodexProfile,
  renameCodexProfile,
  getCodexProfileDeviceAuth,
  getCodexUpdateStatus,
  getAuditGenerationJobsPage,
  getAuditSummary,
  getCodexUsageHistory,
  getProviderPresets,
  getProviderUsage,
  listAdminUsersPage,
  listCodexProfiles,
  resetAdminUserDailyGenerationQuota,
  resetDailyGenerationQuota,
  setActiveProviderPreset,
  startCodexDeviceAuth,
  revokeAdminUserSessions,
  updateAdminUserBan,
  updateAdminUserDailyGenerationLimit,
  updateAdminUserRole,
  updateQuotaPolicy,
  type AdminUser,
  type AuditGenerationJob,
  type AuditSummary,
  type CodexDeviceAuthSession,
  type CodexProfile,
  type CodexUpdateStatus,
  type CodexQuota,
  type QuotaPolicy,
  type ProviderPreset,
  type ProviderUsageSummary,
} from "../api/client";
import { useAuth } from "../features/auth/AuthProvider";

const defaultPolicy: QuotaPolicy = {
  window_hours: 5,
  window_limit: 8,
  parallel_generation_limit: 4,
  codex_usage_min_remaining_percent: 25,
  daily_generation_limit: 20,
  daily_generation_custom_limit_default: 20,
  daily_generation_reset_hour: 6,
};

type TokenUsageSummary = NonNullable<AuditSummary["token_usage"]>;
type ImageUsageSummary = NonNullable<AuditSummary["image_usage"]>;
type AdminUserSortBy = "created_at" | "generation_count" | "remaining_quota";
type AdminUserSortDir = "asc" | "desc";

type TrendPoint = {
  active: number;
  calls: number;
  failed: number;
  images: number;
  images30dAvg: number;
  label: string;
  queued: number;
  succeeded: number;
  tokens: number;
};

type AuditAdminTab = "overview" | "usage" | "jobs" | "quota" | "accounts";

const tabHashById: Record<AuditAdminTab, string> = {
  accounts: "#audit-accounts",
  jobs: "#audit-jobs",
  overview: "#audit-overview",
  quota: "#audit-quota",
  usage: "#audit-usage",
};

const auditTabMeta: Record<AuditAdminTab, { eyebrow: string; subtitle: string; title: string }> = {
  accounts: { eyebrow: "账号", subtitle: "管理用户角色、封禁状态与强制下线。", title: "账号管理" },
  jobs: { eyebrow: "任务", subtitle: "查看最近真实生成记录和输出数量。", title: "生成任务" },
  overview: { eyebrow: "概览", subtitle: "服务器生成状态、队列和用量摘要。", title: "仪表盘" },
  quota: { eyebrow: "策略", subtitle: "调整窗口配额、Codex 剩余额度阈值和并发上限。", title: "配额策略" },
  usage: { eyebrow: "用量", subtitle: "按生成任务汇总令牌消耗和图片产出。", title: "用量统计" },
};

function auditTabFromHash(hash: string): AuditAdminTab {
  const normalized = hash.toLowerCase();
  if (normalized === "#audit-accounts") return "accounts";
  if (normalized === "#audit-usage") return "usage";
  if (normalized === "#audit-jobs") return "jobs";
  if (normalized === "#audit-quota") return "quota";
  return "overview";
}

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

function formatCompact(value?: number | null, unit = "") {
  const safeValue = value ?? 0;
  const abs = Math.abs(safeValue);
  if (abs >= 1_000_000) return `${(safeValue / 1_000_000).toFixed(abs >= 10_000_000 ? 1 : 2)}M${unit}`;
  if (abs >= 1_000) return `${(safeValue / 1_000).toFixed(abs >= 10_000 ? 1 : 2)}K${unit}`;
  return `${formatDecimal(safeValue)}${unit}`;
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
    second: "2-digit",
  });
}

function formatDuration(start?: string | null, end?: string | null) {
  if (!start) return "--";
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return "--";
  const seconds = Math.max(0, Math.round((endMs - startMs) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remain = seconds % 60;
  return `00:${String(minutes).padStart(2, "0")}:${String(remain).padStart(2, "0")}`;
}

function formatAuditGenerationMode(value: string) {
  if (value === "front_design") return "正面生成";
  if (value === "front_revision") return "重生成正视图";
  if (value === "front_local_revision") return "局部生成";
  if (value === "turnaround" || value === "viewset") return "四视图";
  if (value === "generate") return "生成头壳";
  if (value === "image_edit") return "修改图片";
  if (value === "regenerate") return "重新生成";
  if (value === "local_edit") return "局部生成";
  if (value === "discussion") return "对话";
  return value;
}

function statusText(status: string) {
  if (status === "succeeded" || status === "accepted") return "成功";
  if (status === "failed") return "失败";
  if (status === "cancelled") return "已取消";
  if (status === "queued") return "排队中";
  return "运行中";
}

function statusClass(status: string) {
  if (status === "succeeded" || status === "accepted") return "is-success";
  if (status === "failed" || status === "cancelled") return "is-danger";
  if (status === "queued") return "is-warning";
  return "is-running";
}

function getTokenUsage(summary?: AuditSummary | null): TokenUsageSummary {
  return summary?.token_usage ?? {
    cached_input_tokens: null,
    input_tokens: null,
    jobs_with_usage: 0,
    output_tokens: null,
    reasoning_output_tokens: null,
    total_tokens: null,
  };
}

function getImageUsage(summary?: AuditSummary | null): ImageUsageSummary {
  return summary?.image_usage ?? {
    cached_input_tokens_per_image: 0,
    generated_images: 0,
    images_with_token_usage: 0,
    input_tokens_per_image: 0,
    jobs_with_outputs: 0,
    output_tokens_per_image: 0,
    reasoning_output_tokens_per_image: 0,
    total_tokens_per_image: 0,
  };
}

function buildTrendPointsFromSummary(summary?: AuditSummary | null): TrendPoint[] {
  return (summary?.usage_trend ?? []).map((point) => ({
    active: point.active,
    calls: point.calls,
    failed: point.failed,
    images: point.images,
    images30dAvg: point.images_30d_avg,
    label: point.label,
    queued: point.queued,
    succeeded: point.succeeded,
    tokens: point.tokens,
  }));
}

function AdminShellHeader({
  connectionState,
  onRefresh,
  queueLength,
  runningJobs,
}: {
  connectionState: "connected" | "error" | "loading";
  onRefresh: () => void;
  queueLength: number;
  runningJobs: number;
}) {
  const connectionText =
    connectionState === "connected" ? "审计接口已连接" : connectionState === "loading" ? "正在刷新审计数据" : "审计接口异常";
  return (
    <header className="audit-console-header">
      <Group align="center" className="audit-console-brand" gap="md" wrap="nowrap">
        <Image alt="KigCraft" className="audit-console-logo" src={publicUrl("/brand/kigcraft-icon.png")} />
        <Box>
          <Title className="audit-console-title" order={1}>
            KigCraft Admin
          </Title>
        </Box>
      </Group>
      <Group className="audit-console-status" gap="sm" wrap="wrap">
        <span className={`audit-status-pill ${connectionState === "error" ? "is-error" : ""}`}>
          <i className="audit-status-dot" />
          {connectionText}
        </span>
        <span className="audit-status-pill">
          <IconStack2 size={16} />
          排队 {queueLength}
        </span>
        <span className="audit-status-pill">
          <IconActivity size={16} />
          运行中 {runningJobs}
        </span>
      </Group>
      <Group className="audit-console-actions" gap="sm" wrap="nowrap">
        <Button leftSection={<IconRefresh size={16} />} onClick={onRefresh} variant="subtle">
          刷新
        </Button>
        <Button component={Link} leftSection={<IconArrowLeft size={16} />} to="/v2" variant="subtle">
          工作台
        </Button>
      </Group>
    </header>
  );
}

function AdminSidebar({
  activeTab,
  onRefresh,
  onSelectTab,
}: {
  activeTab: AuditAdminTab;
  onRefresh: () => void;
  onSelectTab: (tab: AuditAdminTab) => void;
}) {
  const items = [
    { icon: <IconGauge size={20} />, label: "概览", tab: "overview" },
    { icon: <IconChartBar size={20} />, label: "用量统计", tab: "usage" },
    { icon: <IconHistory size={20} />, label: "生成任务", tab: "jobs" },
    { icon: <IconUsers size={20} />, label: "账号", tab: "accounts" },
    { icon: <IconShieldCheck size={20} />, label: "配额策略", tab: "quota" },
  ];
  return (
    <aside className="audit-console-sidebar">
      <nav>
        {items.map((item) => (
          <button
            key={item.label}
            className={`audit-side-link ${activeTab === item.tab ? "is-active" : ""}`}
            onClick={() => onSelectTab(item.tab as AuditAdminTab)}
            type="button"
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        ))}
      </nav>
      <Box className="audit-sidebar-refresh">
        <Button fullWidth leftSection={<IconRefresh size={16} />} onClick={onRefresh} variant="subtle">
          手动刷新
        </Button>
      </Box>
    </aside>
  );
}

function AdminTabHeading({ activeTab }: { activeTab: AuditAdminTab }) {
  const meta = auditTabMeta[activeTab];
  return (
    <Box className="audit-tab-heading">
      <span>{meta.eyebrow}</span>
      <Box>
        <Title order={2}>{meta.title}</Title>
        <Text>{meta.subtitle}</Text>
      </Box>
    </Box>
  );
}

function MetricCard({
  icon,
  label,
  trendValues,
  value,
}: {
  icon: ReactNode;
  label: string;
  trendValues: number[];
  value: string;
}) {
  const option = useMemo(
    () => buildSparklineOption({ color: "#c9552f", name: label, values: trendValues }),
    [label, trendValues],
  );
  return (
    <Paper className="audit-kpi-card" radius={0}>
      <Group align="flex-start" gap="md" wrap="nowrap">
        <Box className="audit-kpi-icon">{icon}</Box>
        <Stack gap={0.25}>
          <Text fw={900} size="sm">
            {label}
          </Text>
          <Text className="audit-kpi-value">{value}</Text>
        </Stack>
      </Group>
      <EChart ariaLabel={`${label}趋势`} className="audit-sparkline" option={option} />
    </Paper>
  );
}

export function formatTrendXAxisLabel(label: string): string {
  const [, timeLabel] = label.split(" ");
  return timeLabel || label;
}

const REMAINING_RANGES = [
  { hours: 24, label: "24 小时" },
  { hours: 168, label: "7 天" },
  { hours: 720, label: "30 天" },
] as const;

function quotaWindowLabel(minutes: number | null | undefined): string {
  if (!minutes) return "额度窗口";
  if (minutes % 1440 === 0) return `${minutes / 1440} 天窗口`;
  if (minutes % 60 === 0) return `${minutes / 60} 小时窗口`;
  return `${minutes} 分钟窗口`;
}

/** Remaining Codex quota over time, per rate-limit window. */
function CodexRemainingChart() {
  const [hours, setHours] = useState<number>(168);
  const query = useQuery({
    queryKey: ["codex-usage-history", hours],
    queryFn: () => getCodexUsageHistory(hours),
    refetchInterval: 5 * 60 * 1000,
    retry: false,
  });
  const points = query.data?.points ?? [];
  const option = useMemo(() => {
    const primary = points.map((point) => [Date.parse(point.at), point.primary_remaining_percent] as [number, number]);
    const secondary = points
      .filter((point) => point.secondary_remaining_percent != null)
      .map((point) => [Date.parse(point.at), point.secondary_remaining_percent as number] as [number, number]);
    const primaryMinutes = points.at(-1)?.primary_window_minutes;
    const secondaryMinutes = [...points].reverse().find((point) => point.secondary_window_minutes != null)?.secondary_window_minutes;
    return buildRemainingQuotaOption({
      series: [
        { color: "#c9552f", data: primary, name: `${quotaWindowLabel(primaryMinutes)}剩余` },
        ...(secondary.length ? [{ color: "#191f23", data: secondary, name: `${quotaWindowLabel(secondaryMinutes)}剩余` }] : []),
      ],
    });
  }, [points]);
  const latest = points.at(-1);
  return (
    <Box className="audit-remaining-quota" data-testid="audit-codex-remaining">
      <Group gap="xs" justify="space-between" wrap="wrap">
        <Group gap="xs">
          <Title order={3} size="h5">
            Codex 剩余用量
          </Title>
          {latest ? <Text size="sm">当前 {latest.primary_remaining_percent.toFixed(1)}%</Text> : null}
        </Group>
        <Group gap={6}>
          {REMAINING_RANGES.map((range) => (
            <Button
              aria-pressed={hours === range.hours}
              key={range.hours}
              onClick={() => setHours(range.hours)}
              size="xs"
              variant={hours === range.hours ? "filled" : "light"}
            >
              {range.label}
            </Button>
          ))}
        </Group>
      </Group>
      <Box className="audit-chart-frame">
        <EChart ariaLabel="Codex 剩余用量曲线" className="audit-line-chart" option={option} />
        {!points.length ? <Box className="audit-chart-empty-state">{query.isError ? "暂时无法读取剩余用量记录" : "暂无记录，之后每几分钟采样一次"}</Box> : null}
      </Box>
    </Box>
  );
}

function UsageTrendChart({
  id = "audit-usage",
  points,
  primaryLabel = "令牌用量",
  secondaryKey = "calls",
  secondaryLabel = "生成任务数",
  title = "用量趋势",
}: {
  id?: string;
  points: TrendPoint[];
  primaryLabel?: string;
  secondaryKey?: "calls" | "images";
  secondaryLabel?: string;
  title?: string;
}) {
  const chartOptions = useMemo(
    () =>
      buildDualTrendOption({
        labels: points.map((point) => point.label),
        primary: { color: "#c9552f", data: points.map((point) => point.tokens), name: primaryLabel },
        secondary: {
          color: "#191f23",
          data: points.map((point) => point[secondaryKey]),
          name: secondaryLabel,
        },
        tertiary: {
          color: "#d69224",
          data: points.map((point) => point.images30dAvg),
          name: "30 天日均图片产出",
        },
      }),
    [points, primaryLabel, secondaryKey, secondaryLabel],
  );
  return (
    <Paper className="audit-dashboard-card audit-usage-chart-card" id={id} radius={0}>
      <Group justify="space-between" wrap="wrap">
        <Box>
          <Group gap="xs">
            <Title order={2} size="h4">
              {title}
            </Title>
            <IconActivity size={16} />
          </Group>
        </Box>
      </Group>

      <Box className="audit-chart-frame" data-testid="audit-trend-chart">
        <EChart ariaLabel={`${title}图`} className="audit-line-chart" option={chartOptions} />
        {!points.length ? <Box className="audit-chart-empty-state">暂无趋势数据</Box> : null}
      </Box>
      <CodexRemainingChart />
    </Paper>
  );
}

function QueueHealthPanel({
  activeJobs,
  failureRate,
  parallelLimit,
  parallelUsed,
  queueLength,
}: {
  activeJobs: number;
  failureRate: number;
  parallelLimit: number;
  parallelUsed: number;
  queueLength: number;
}) {
  const rows = [
    { icon: <IconClock size={18} />, label: "等待中任务", value: queueLength },
    { icon: <IconActivity size={18} />, label: "处理中任务", value: activeJobs },
    { icon: <IconStack2 size={18} />, label: "并发占用", value: `${parallelUsed} / ${parallelLimit}` },
    { icon: <IconRefresh size={18} />, label: "失败率", value: formatPercent(failureRate) },
  ];
  return (
    <Paper className="audit-dashboard-card audit-side-card" radius={0}>
      <Group justify="space-between">
        <Title order={2} size="h4">
          队列健康
        </Title>
      </Group>
      <Stack gap={0}>
        {rows.map((row) => (
          <Group key={row.label} className="audit-health-row" justify="space-between" wrap="nowrap">
            <Group gap="sm" wrap="nowrap">
              {row.icon}
              <span>{row.label}</span>
            </Group>
            <b>{row.value}</b>
          </Group>
        ))}
      </Stack>
    </Paper>
  );
}

function formatResetLabel(window?: CodexQuota["five_hour"] | null) {
  if (!window) return "—";
  if (window.reset_at) {
    const parsed = new Date(window.reset_at);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    }
  }
  if (typeof window.reset_after_seconds === "number" && window.reset_after_seconds > 0) {
    const minutes = Math.max(1, Math.ceil(window.reset_after_seconds / 60));
    if (minutes < 60) return `${minutes} 分钟后`;
    const hours = Math.floor(minutes / 60);
    const restMinutes = minutes % 60;
    return restMinutes === 0 ? `${hours} 小时后` : `${hours} 小时 ${restMinutes} 分钟后`;
  }
  return "—";
}

function CodexQuotaPanel({ codexQuota }: { codexQuota?: CodexQuota | null }) {
  const fiveHour = codexQuota?.five_hour ?? null;
  const weekly = codexQuota?.weekly ?? null;
  return (
    <Paper className="audit-dashboard-card audit-side-card" data-testid="codex-quota-panel" radius={0}>
      <Group justify="space-between">
        <Title order={2} size="h4">
          Codex 额度
        </Title>
        {codexQuota?.plan_type ? (
          <Badge color="blue" variant="light">
            {codexQuota.plan_type}
          </Badge>
        ) : null}
      </Group>
      {!codexQuota?.available ? (
        <Stack gap="xs" mt="sm">
          <Text c="var(--kb-concrete-grey)" size="sm">
            暂无法获取 Codex CLI 余量数据
          </Text>
          {codexQuota?.error ? (
            <Text c="var(--kb-concrete-grey)" size="xs">
              {codexQuota.error}
            </Text>
          ) : null}
        </Stack>
      ) : (
        <Box className="audit-quota-table" mt="sm">
          <Group justify="space-between">
            <span>5 小时窗口剩余</span>
            <b>{typeof fiveHour?.remaining_percent === "number" ? `${fiveHour.remaining_percent.toFixed(1)}%` : "—"}</b>
          </Group>
          <Group justify="space-between">
            <span>5 小时重置时间</span>
            <b>{formatResetLabel(fiveHour)}</b>
          </Group>
          <Group justify="space-between">
            <span>每周窗口剩余</span>
            <b>{typeof weekly?.remaining_percent === "number" ? `${weekly.remaining_percent.toFixed(1)}%` : "—"}</b>
          </Group>
          <Group justify="space-between">
            <span>每周重置时间</span>
            <b>{formatResetLabel(weekly)}</b>
          </Group>
        </Box>
      )}
    </Paper>
  );
}

function CompactQuotaPanel({
  draftPolicy,
}: {
  draftPolicy: QuotaPolicy;
}) {
  return (
    <Paper className="audit-dashboard-card audit-side-card" id="audit-quota" radius={0}>
      <Group justify="space-between">
        <Title order={2} size="h4">
          配额策略
        </Title>
      </Group>
      <Box className="audit-quota-table">
        <Group justify="space-between">
          <span>默认策略</span>
          <Badge color="green" variant="light">
            生效中
          </Badge>
        </Group>
        <Group justify="space-between">
          <span>统计窗口</span>
          <b>{draftPolicy.window_hours} 小时</b>
        </Group>
        <Group justify="space-between">
          <span>窗口配额</span>
          <b>{draftPolicy.window_limit}</b>
        </Group>
        <Group justify="space-between">
          <span>Codex 5h 剩余额度阈值</span>
          <b>{draftPolicy.codex_usage_min_remaining_percent}%</b>
        </Group>
        <Group justify="space-between">
          <span>并发任务上限</span>
          <b>{draftPolicy.parallel_generation_limit}</b>
        </Group>
      </Box>
    </Paper>
  );
}

function RecentJobsTable({
  detailMode = false,
  hasMore = false,
  id = "audit-jobs",
  jobs,
  loadingMore = false,
  onLoadMore,
  onRefresh,
}: {
  detailMode?: boolean;
  hasMore?: boolean;
  id?: string;
  jobs: AuditGenerationJob[];
  loadingMore?: boolean;
  onLoadMore?: () => void;
  onRefresh: () => void;
}) {
  const rows = detailMode ? jobs : jobs.slice(0, 5);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const selectedJob = rows.find((job) => job.id === selectedJobId) ?? rows[0] ?? null;

  useEffect(() => {
    if (!detailMode) return;
    if (!rows.length) {
      setSelectedJobId(null);
      return;
    }
    if (!selectedJobId || !rows.some((job) => job.id === selectedJobId)) {
      setSelectedJobId(rows[0].id);
    }
  }, [detailMode, rows, selectedJobId]);

  return (
    <Box className={detailMode ? "audit-job-records-layout" : undefined} id={id}>
      <Paper className="audit-dashboard-card audit-job-table-card" radius={0}>
        <Group className="audit-table-title" justify="space-between">
          <Group gap="sm">
            <Title order={2} size="h4">
              最近生成任务
            </Title>
            <Button leftSection={<IconRefresh size={15} />} onClick={onRefresh} variant="subtle">
              刷新
            </Button>
          </Group>
        </Group>
        <Box className="audit-table-wrap">
          <table className="audit-job-table">
            <thead>
              <tr>
                <th>创建时间</th>
                <th>结果图</th>
                <th>Provider</th>
                <th>类型</th>
                <th>状态</th>
                <th>令牌用量</th>
                <th>输出</th>
                <th>耗时</th>
              </tr>
            </thead>
            <tbody>
              {rows.length ? (
                rows.map((job) => {
                  const isSelected = detailMode && selectedJob?.id === job.id;
                  return (
                    <tr
                      aria-current={isSelected ? "true" : undefined}
                      className={detailMode ? `audit-job-row ${isSelected ? "is-selected" : ""}` : undefined}
                      data-testid={`audit-job-row-${job.id}`}
                      key={job.id}
                      onClick={detailMode ? () => setSelectedJobId(job.id) : undefined}
                      onKeyDown={
                        detailMode
                          ? (event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                setSelectedJobId(job.id);
                              }
                            }
                          : undefined
                      }
                      tabIndex={detailMode ? 0 : undefined}
                    >
                      <td>{formatDateTime(job.created_at)}</td>
                      <td>
                        {job.outputs.length ? (
                          <Box className="audit-output-thumbs">
                            {job.outputs.slice(0, 3).map((output) => (
                              <img alt={`结果图 ${output.index}`} key={`${job.id}-${output.index}`} src={output.image_url} />
                            ))}
                            {job.outputs.length > 3 ? <span>+{job.outputs.length - 3}</span> : null}
                          </Box>
                        ) : (
                          <span className="audit-empty-thumb">--</span>
                        )}
                      </td>
                      <td>{job.provider}</td>
                      <td>{formatAuditGenerationMode(job.generation_mode)}</td>
                      <td>
                        <span className={`audit-status-label ${statusClass(job.status)}`}>{statusText(job.status)}</span>
                      </td>
                      <td>{formatCompact(job.token_usage?.total_tokens)}</td>
                      <td>{job.outputs.length}</td>
                      <td>{formatDuration(job.created_at, job.updated_at)}</td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={8}>暂无生成任务记录。</td>
                </tr>
              )}
            </tbody>
          </table>
        </Box>
        {detailMode && hasMore ? (
          <Group justify="center" mt="md">
            <Button loading={loadingMore} onClick={onLoadMore} variant="light">
              加载更多
            </Button>
          </Group>
        ) : null}
      </Paper>
      {detailMode ? <GenerationJobDetail job={selectedJob} /> : null}
    </Box>
  );
}

function GenerationJobDetail({ job }: { job: AuditGenerationJob | null }) {
  if (!job) {
    return (
      <Paper className="audit-dashboard-card audit-generation-job-detail" data-testid="audit-job-detail-empty" radius={0}>
        <Title order={2} size="h4">
          任务详情
        </Title>
        <Text c="var(--kb-concrete-grey)" fw={800}>
          暂无可查看的任务。
        </Text>
      </Paper>
    );
  }

  return (
    <Paper className="audit-dashboard-card audit-generation-job-detail" data-testid="audit-job-detail" radius={0}>
      <Group justify="space-between" wrap="wrap">
        <Box>
          <Title order={2} size="h4">
            任务详情
          </Title>
          <Text className="audit-job-detail-id">{job.id}</Text>
        </Box>
        <span className={`audit-status-label ${statusClass(job.status)}`}>{statusText(job.status)}</span>
      </Group>

      <Box className="audit-job-detail-grid">
        <MetricRow icon={<IconHistory size={20} />} label="类型" value={formatAuditGenerationMode(job.generation_mode)} />
        <MetricRow icon={<IconDatabase size={20} />} label="Provider" value={job.provider} />
        <MetricRow icon={<IconActivity size={20} />} label="进度" value={`${job.progress}%`} />
        <MetricRow icon={<IconClock size={20} />} label="耗时" value={formatDuration(job.created_at, job.updated_at)} />
      </Box>

      <JobImageStrip items={job.references} title="提交参考图" />
      <JobImageStrip items={job.outputs} title="输出图片" />

      <Box className="audit-job-prompt-block">
        <Title order={3} size="h5">
          提交文字
        </Title>
        <Text>{job.user_notes || job.user_requirements || "未记录提交文字。"}</Text>
      </Box>

      {job.failure_reason ? (
        <Box className="audit-job-prompt-block">
          <Title order={3} size="h5">
            失败原因
          </Title>
          <Text c="red">{job.failure_reason}</Text>
        </Box>
      ) : null}

      <Box className="audit-job-prompt-block">
        <Title order={3} size="h5">
          用量
        </Title>
        <Text>总 Token：{formatCompact(job.token_usage?.total_tokens)}</Text>
        <Text>输入：{formatCompact(job.token_usage?.input_tokens)} / 输出：{formatCompact(job.token_usage?.output_tokens)}</Text>
      </Box>
    </Paper>
  );
}

function JobImageStrip({
  items,
  title,
}: {
  items: Array<{ description?: string | null; image_url?: string | null; index?: number; kind?: string | null }>;
  title: string;
}) {
  return (
    <Box className="audit-job-image-section">
      <Title order={3} size="h5">
        {title}
      </Title>
      {items.length ? (
        <Box className="audit-job-image-grid">
          {items.map((item, index) => (
            <figure key={`${item.image_url ?? title}-${index}`}>
              {item.image_url ? <img alt={item.description || `${title} ${index + 1}`} src={item.image_url} /> : <span className="audit-empty-thumb">--</span>}
              <figcaption>{item.description || item.kind || (item.index ? `#${item.index}` : `#${index + 1}`)}</figcaption>
            </figure>
          ))}
        </Box>
      ) : (
        <Text c="var(--kb-concrete-grey)" fw={800}>
          暂无记录。
        </Text>
      )}
    </Box>
  );
}

function UsageDetailPanels({
  failedJobs,
  imageUsage,
  latestJobUpdatedAt,
  onRefresh,
  tokenUsage,
  windowHours,
}: {
  failedJobs: number;
  imageUsage: ImageUsageSummary;
  latestJobUpdatedAt?: string | null;
  onRefresh: () => void;
  tokenUsage: TokenUsageSummary;
  windowHours: number;
}) {
  const latest = latestJobUpdatedAt;
  const totalTokens = tokenUsage.total_tokens ?? 0;
  const generatedImages = imageUsage.generated_images ?? 0;
  return (
    <Stack className="audit-usage-details" gap="md">
      <Paper className="audit-mobile-detail-card" radius={0}>
        <Group justify="space-between">
          <Title order={2} size="h4">
            Token 明细
          </Title>
        </Group>
        <MetricRow icon={<IconDatabase size={22} />} label="总 Token" value={formatCompact(totalTokens)} />
        <MetricRow icon={<IconActivity size={22} />} label="输入 Token" value={formatCompact(tokenUsage.input_tokens)} />
        <MetricRow icon={<IconActivity size={22} />} label="输出 Token" value={formatCompact(tokenUsage.output_tokens)} />
        <MetricRow icon={<IconClock size={22} />} label="有用量记录任务" value={`${formatInteger(tokenUsage.jobs_with_usage)} 个`} />
      </Paper>
      <Paper className="audit-mobile-detail-card" radius={0}>
        <Group justify="space-between">
          <Title order={2} size="h4">
            图片产出明细
          </Title>
        </Group>
        <MetricRow icon={<IconPhoto size={22} />} label="总产出图片" value={`${formatInteger(generatedImages)} 张`} />
        <MetricRow icon={<IconCheck size={22} />} label="有输出任务" value={`${formatInteger(imageUsage.jobs_with_outputs)} 个`} />
        <MetricRow icon={<IconX size={22} />} label="失败任务" value={`${formatInteger(failedJobs)} 个`} />
        <MetricRow icon={<IconClock size={22} />} label="配额统计窗口" value={`${windowHours} 小时`} />
      </Paper>
      <Group className="audit-mobile-refresh" justify="space-between">
        <Group gap="sm">
          <IconRefresh size={20} />
          <span>最后更新：{latest ? formatDateTime(latest) : "暂无任务记录"}</span>
        </Group>
        <Button leftSection={<IconRefresh size={18} />} onClick={onRefresh} variant="subtle">
          刷新数据
        </Button>
      </Group>
    </Stack>
  );
}

function providerName(provider: string) {
  if (provider === "seedream") return "Seedream";
  if (provider === "qwen") return "Qwen";
  if (provider === "codex") return "Codex";
  return provider;
}

function ProviderPresetAndUsagePanel({
  activePresetId,
  onPresetChange,
  onSave,
  presets,
  saving,
  usage,
}: {
  activePresetId: ProviderPreset["id"];
  onPresetChange: (presetId: ProviderPreset["id"]) => void;
  onSave: () => void;
  presets: ProviderPreset[];
  saving: boolean;
  usage: ProviderUsageSummary[];
}) {
  return (
    <Stack gap="md" mt="md">
      <Paper className="audit-dashboard-card" radius={0}>
        <Title order={2} size="h4">
          生成提供方预设
        </Title>
        <Stack gap="sm" mt="sm">
          <Text component="label" htmlFor="provider-preset-select" size="sm">
            生成提供方预设
          </Text>
          <select
            id="provider-preset-select"
            onChange={(event) => onPresetChange(event.currentTarget.value as ProviderPreset["id"])}
            value={activePresetId}
          >
            {presets.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.label} · {preset.analysis_provider} / {preset.image_provider}
              </option>
            ))}
          </select>
          <Group justify="flex-end">
            <Button leftSection={<IconDeviceFloppy size={16} />} loading={saving} onClick={onSave}>
              保存提供方预设
            </Button>
          </Group>
        </Stack>
      </Paper>

      <Stack gap="sm">
        <Title order={2} size="h4">
          提供方用量明细
        </Title>
        {usage.length ? (
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
            {usage.map((summary) => (
              <Paper key={summary.provider} className="audit-dashboard-card" radius={0}>
                <Title order={3} size="h5">
                  {providerName(summary.provider)}
                </Title>
                <Text size="sm">调用：{formatInteger(summary.calls)}</Text>
                <Text size="sm">成功：{formatInteger(summary.successes)} / 失败：{formatInteger(summary.failures)}</Text>
                <Text size="sm">输入 Token：{formatCompact(summary.input_tokens)}</Text>
                <Text size="sm">输出 Token：{formatCompact(summary.output_tokens)}</Text>
                <Text size="sm">总 Token：{formatCompact(summary.total_tokens)}</Text>
                <Text size="sm">生成图片：{formatInteger(summary.generated_images)}</Text>
              </Paper>
            ))}
          </SimpleGrid>
        ) : (
          <Text c="dimmed" size="sm">暂无提供方用量记录。</Text>
        )}
      </Stack>
    </Stack>
  );
}

function MetricRow({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <Group className="audit-mobile-metric-row" justify="space-between" wrap="nowrap">
      <Group gap="sm" wrap="nowrap">
        {icon}
        <span>{label}</span>
      </Group>
      <Group gap="md" wrap="nowrap">
        <b>{value}</b>
      </Group>
    </Group>
  );
}

function MobileBottomNav({
  activeTab,
  onSelectTab,
}: {
  activeTab: AuditAdminTab;
  onSelectTab: (tab: AuditAdminTab) => void;
}) {
  const items = [
    { icon: <IconHome size={26} />, label: "概览", tab: "overview" },
    { icon: <IconChartBar size={26} />, label: "用量", tab: "usage" },
    { icon: <IconHistory size={26} />, label: "任务", tab: "jobs" },
    { icon: <IconUsers size={26} />, label: "账号", tab: "accounts" },
    { icon: <IconShieldCheck size={26} />, label: "配额", tab: "quota" },
  ];
  return (
    <nav className="audit-bottom-nav" data-testid="audit-mobile-tabs">
      {items.map((item) => (
        <button
          key={item.label}
          className={activeTab === item.tab ? "is-active" : ""}
          onClick={() => onSelectTab(item.tab as AuditAdminTab)}
          type="button"
        >
          {item.icon}
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}

function QuotaEditor({
  draftPolicy,
  id = "audit-quota",
  onResetDailyQuota,
  resetDailyQuotaPending,
  saveMessage,
  saveMutation,
  setDraftPolicy,
}: {
  draftPolicy: QuotaPolicy;
  id?: string;
  onResetDailyQuota: () => void;
  resetDailyQuotaPending: boolean;
  saveMessage: string | null;
  saveMutation: UseMutationResult<QuotaPolicy, Error, QuotaPolicy>;
  setDraftPolicy: Dispatch<SetStateAction<QuotaPolicy>>;
}) {
  return (
    <Paper className="audit-dashboard-card audit-quota-editor" id={id} radius={0}>
      <Title order={2} size="h4">
        配额参数
      </Title>
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
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
          data-testid="quota-window-limit"
          label="窗口配额"
          onChange={(value) =>
            setDraftPolicy((current) => ({
              ...current,
              window_limit: toNumberInputValue(value, current.window_limit),
            }))
          }
          value={draftPolicy.window_limit}
        />
        <NumberInput
          data-testid="quota-codex-usage-min-remaining-percent"
          label="Codex 5h 剩余额度拒绝阈值（%）"
          onChange={(value) =>
            setDraftPolicy((current) => ({
              ...current,
              codex_usage_min_remaining_percent: toNumberInputValue(value, current.codex_usage_min_remaining_percent),
            }))
          }
          value={draftPolicy.codex_usage_min_remaining_percent}
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
        <NumberInput
          data-testid="quota-daily-generation-limit"
          label="每日生成额度"
          onChange={(value) =>
            setDraftPolicy((current) => ({
              ...current,
              daily_generation_limit: toNumberInputValue(value, current.daily_generation_limit),
            }))
          }
          value={draftPolicy.daily_generation_limit}
        />
        <NumberInput
          data-testid="quota-daily-generation-custom-limit-default"
          label="自定义每日额度默认值"
          onChange={(value) =>
            setDraftPolicy((current) => ({
              ...current,
              daily_generation_custom_limit_default: toNumberInputValue(
                value,
                current.daily_generation_custom_limit_default,
              ),
            }))
          }
          value={draftPolicy.daily_generation_custom_limit_default}
        />
      </SimpleGrid>
      <Text c="dimmed" size="sm">
        每日 {String(draftPolicy.daily_generation_reset_hour).padStart(2, "0")}:00 重置
      </Text>
      <Group justify="flex-end" wrap="wrap">
        <Button
          data-testid="quota-daily-generation-reset-all"
          leftSection={<IconRefresh size={16} />}
          loading={resetDailyQuotaPending}
          onClick={onResetDailyQuota}
          variant="subtle"
        >
          重置今日额度
        </Button>
        <Button
          data-testid="quota-policy-save"
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
    </Paper>
  );
}

const ANSI_ESCAPE_SEQUENCE = new RegExp("\\x1B\\[[0-?]*[ -/]*[@-~]", "g");

function stripAnsiEscapeSequences(value: string) {
  return value.replace(ANSI_ESCAPE_SEQUENCE, "");
}

function CodexProfilesPanel({
  updateStatus,
  deviceSession,
  isDeleting,
  isLoading,
  isStarting,
  onActivate,
  onCancelDeviceAuth,
  onDelete,
  onRename,
  onRefresh,
  onRefreshDeviceAuth,
  onStartDeviceAuth,
  profiles,
}: {
  updateStatus?: CodexUpdateStatus;
  deviceSession: CodexDeviceAuthSession | null;
  isDeleting: boolean;
  isLoading: boolean;
  isStarting: boolean;
  onActivate: (profileId: string) => Promise<unknown>;
  onCancelDeviceAuth: (profileId: string, sessionId: string) => Promise<CodexDeviceAuthSession>;
  onDelete: (profileId: string) => Promise<unknown>;
  onRename: (profileId: string, name: string) => Promise<unknown>;
  onRefresh: () => void;
  onRefreshDeviceAuth: (profileId: string, sessionId: string) => Promise<CodexDeviceAuthSession>;
  onStartDeviceAuth: () => Promise<CodexDeviceAuthSession>;
  profiles: CodexProfile[];
}) {
  const [localDeviceSession, setLocalDeviceSession] = useState<CodexDeviceAuthSession | null>(deviceSession);
  const [pendingDeleteProfile, setPendingDeleteProfile] = useState<CodexProfile | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const submitRename = async () => {
    if (!renaming || !renaming.name.trim()) return;
    await onRename(renaming.id, renaming.name.trim());
    setRenaming(null);
  };
  const deviceSessionRef = useRef<CodexDeviceAuthSession | null>(deviceSession);
  const refreshInFlightGenerationRef = useRef<number | null>(null);
  const sessionGenerationRef = useRef(0);

  const updateDeviceSession = (session: CodexDeviceAuthSession | null) => {
    deviceSessionRef.current = session;
    setLocalDeviceSession(session);
  };

  useEffect(() => {
    updateDeviceSession(deviceSession);
  }, [deviceSession]);

  const startDeviceAuth = async () => {
    if (isStarting || deviceSessionRef.current?.status === "running") return;
    sessionGenerationRef.current += 1;
    updateDeviceSession(await onStartDeviceAuth());
  };

  const refreshDeviceAuth = async () => {
    const session = deviceSessionRef.current;
    if (!session || session.status !== "running") return;
    const generation = sessionGenerationRef.current;
    if (refreshInFlightGenerationRef.current === generation) return;
    refreshInFlightGenerationRef.current = generation;
    try {
      const refreshed = await onRefreshDeviceAuth(session.profile_id, session.id);
      const current = deviceSessionRef.current;
      if (sessionGenerationRef.current === generation && current?.id === session.id && current.status === "running") {
        updateDeviceSession(refreshed);
      }
    } finally {
      if (refreshInFlightGenerationRef.current === generation) {
        refreshInFlightGenerationRef.current = null;
      }
    }
  };

  const cancelDeviceAuth = async () => {
    const session = deviceSessionRef.current;
    if (!session || session.status !== "running") return;
    sessionGenerationRef.current += 1;
    updateDeviceSession(await onCancelDeviceAuth(session.profile_id, session.id));
  };

  useEffect(() => {
    if (!localDeviceSession || localDeviceSession.status !== "running") return;
    const timer = window.setInterval(() => void refreshDeviceAuth(), 1000);
    return () => window.clearInterval(timer);
  }, [localDeviceSession?.id, localDeviceSession?.status]);

  return (
    <Paper className="audit-dashboard-card audit-quota-editor" data-testid="codex-profiles-panel" radius={0}>
      <Group justify="space-between" wrap="wrap">
        <Title order={2} size="h4">
          Codex 账号
        </Title>
        <Button leftSection={<IconRefresh size={15} />} loading={isLoading} onClick={onRefresh} variant="subtle">
          刷新
        </Button>
      </Group>

      <Group justify="space-between" wrap="wrap">
        <Text fw={700}>{updateStatus?.version_after ?? updateStatus?.version_before ?? "Codex CLI 版本未知"}</Text>
        {updateStatus?.available && updateStatus.succeeded ? <Badge color="green">更新正常</Badge> : null}
      </Group>
      {updateStatus?.available && updateStatus.succeeded === false ? (
        <Alert color="yellow">Codex CLI 自动更新异常：{updateStatus.error ?? "update_failed"}</Alert>
      ) : null}

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        <Stack gap="sm">
          <Text fw={700}>账号列表</Text>
          {profiles.length ? (
            profiles.map((profile) => (
              <Group key={profile.id} justify="space-between" wrap="wrap">
                <Stack gap={0}>
                  {renaming?.id === profile.id ? (
                    <Group gap="xs" wrap="nowrap">
                      <input
                        aria-label="账号名称"
                        autoFocus
                        maxLength={120}
                        onChange={(event) => setRenaming({ id: profile.id, name: event.target.value })}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") void submitRename();
                          if (event.key === "Escape") setRenaming(null);
                        }}
                        value={renaming.name}
                      />
                      <Button onClick={() => void submitRename()} size="xs" variant="subtle">保存</Button>
                      <Button onClick={() => setRenaming(null)} size="xs" variant="subtle">取消</Button>
                    </Group>
                  ) : (
                    <Group gap="xs">
                      <Text fw={700}>{profile.name}</Text>
                      {profile.is_active ? <Badge color="green" variant="light">当前</Badge> : null}
                    </Group>
                  )}
                  {profile.account_email && profile.account_email !== profile.name ? (
                    <Text c="dimmed" size="xs">{profile.account_email}</Text>
                  ) : null}
                  <Text c="dimmed" size="xs">
                    {profile.is_builtin ? "codex-home" : profile.id}
                    {" · "}
                    {profile.credential_files.length ? profile.credential_files.join(", ") : "未导入凭证"}
                  </Text>
                  <Text c="dimmed" size="xs">
                    添加时间：{formatDateTime(profile.created_at)}
                  </Text>
                </Stack>
                <Group gap="xs">
                  <Button
                    disabled={profile.is_active || profile.credential_files.length === 0}
                    onClick={() => void onActivate(profile.id)}
                    size="xs"
                    variant="subtle"
                  >
                    切换
                  </Button>
                  {!profile.is_builtin ? (
                    <Button
                      onClick={() => setRenaming({ id: profile.id, name: profile.name })}
                      size="xs"
                      variant="subtle"
                    >
                      重命名
                    </Button>
                  ) : null}
                  {!profile.is_builtin ? (
                    <Button
                      color="red"
                      data-testid={`codex-profile-delete-${profile.id}`}
                      disabled={
                        isDeleting
                        || (localDeviceSession?.status === "running" && localDeviceSession.profile_id === profile.id)
                      }
                      onClick={() => setPendingDeleteProfile(profile)}
                      size="xs"
                      variant="subtle"
                    >
                      删除
                    </Button>
                  ) : null}
                </Group>
              </Group>
            ))
          ) : (
            <Text c="dimmed" size="sm">暂无 Codex 账号</Text>
          )}
        </Stack>

        <Stack gap="sm">
          <Text fw={700}>登录新账号</Text>
          <Text c="dimmed" size="sm">启动设备授权后，按提示在浏览器中完成 Codex 登录。</Text>
          <Button
            disabled={isStarting || localDeviceSession?.status === "running"}
            loading={isStarting}
            onClick={() => void startDeviceAuth()}
          >
            添加 Codex 登录
          </Button>
        </Stack>
      </SimpleGrid>

      {localDeviceSession ? (
        <Stack gap="sm" mt="md">
          <Group justify="space-between" wrap="wrap">
            <Text fw={700}>设备登录状态：{localDeviceSession.status}</Text>
            <Group gap="xs">
              {localDeviceSession.status === "running" ? (
                <Button color="red" onClick={() => void cancelDeviceAuth()} size="xs" variant="subtle">
                  取消登录
                </Button>
              ) : null}
            </Group>
          </Group>
          {localDeviceSession.output ? (
            <Text component="pre" size="sm" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
              {stripAnsiEscapeSequences(localDeviceSession.output)}
            </Text>
          ) : null}
          {localDeviceSession.error ? <Alert color="red">{localDeviceSession.error}</Alert> : null}
        </Stack>
      ) : null}

      <Dialog onClose={() => setPendingDeleteProfile(null)} open={pendingDeleteProfile !== null}>
        <DialogTitle>删除 Codex 账号</DialogTitle>
        <DialogContent>
          <Text>
            确定删除账号 {pendingDeleteProfile?.name}（{pendingDeleteProfile?.id}）吗？此操作不可恢复。
          </Text>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPendingDeleteProfile(null)} variant="subtle">取消</Button>
          <Button
            color="red"
            loading={isDeleting}
            onClick={() => {
              if (!pendingDeleteProfile) return;
              void onDelete(pendingDeleteProfile.id).then(() => setPendingDeleteProfile(null));
            }}
          >
            确认删除
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
}

type AdminUserRole = AdminUser["role"];

type PendingAdminUserAction =
  | { isBanned: boolean; type: "ban"; user: AdminUser }
  | { role: AdminUserRole; type: "role"; user: AdminUser }
  | { type: "revoke_sessions"; user: AdminUser };

function roleText(role: AdminUserRole) {
  return role === "admin" ? "管理员" : "用户";
}

function banText(user: AdminUser) {
  return user.is_banned ? "已封禁" : "可使用";
}

function pendingAdminUserActionTitle(action: PendingAdminUserAction) {
  switch (action.type) {
    case "ban":
      return action.isBanned ? "确认封禁账号" : "确认解封账号";
    case "role":
      return action.role === "admin" ? "确认设为管理员" : "确认设为用户";
    case "revoke_sessions":
      return "确认强制下线";
  }
}

function pendingAdminUserActionMessage(action: PendingAdminUserAction) {
  switch (action.type) {
    case "ban":
      return action.isBanned
        ? `确定封禁 ${action.user.email} 吗？封禁后该账号将无法登录。`
        : `确定解封 ${action.user.email} 吗？解封后该账号可恢复正常使用。`;
    case "role":
      return action.role === "admin"
        ? `确定将 ${action.user.email} 设为管理员吗？`
        : `确定将 ${action.user.email} 设为用户吗？`;
    case "revoke_sessions":
      return `确定强制 ${action.user.email} 下线吗？该账号的所有设备将被登出，需要重新登录。`;
  }
}

function pendingAdminUserActionConfirmLabel(action: PendingAdminUserAction) {
  switch (action.type) {
    case "ban":
      return action.isBanned ? "封禁" : "解封";
    case "role":
      return action.role === "admin" ? "设为管理员" : "设为用户";
    case "revoke_sessions":
      return "强制下线";
  }
}

function AdminUserActionConfirmDialog({
  action,
  isPending,
  onCancel,
  onConfirm,
}: {
  action: PendingAdminUserAction | null;
  isPending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog onClose={onCancel} open={Boolean(action)}>
      <DialogTitle>{action ? pendingAdminUserActionTitle(action) : ""}</DialogTitle>
      <DialogContent>
        <Text>{action ? pendingAdminUserActionMessage(action) : ""}</Text>
      </DialogContent>
      <DialogActions>
        <Button disabled={isPending} onClick={onCancel} variant="subtle">
          取消
        </Button>
        <Button
          aria-label={action ? `确认${pendingAdminUserActionConfirmLabel(action)}` : "确认"}
          color={action?.type === "ban" && action.isBanned ? "red" : undefined}
          loading={isPending}
          onClick={onConfirm}
          variant="filled"
        >
          {action ? pendingAdminUserActionConfirmLabel(action) : "确认"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function AdminUsersPanel({
  defaultCustomDailyGenerationLimit,
  hasMore,
  isError,
  isLoading,
  isLoadingMore,
  onBanChange,
  onDailyQuotaAdjust,
  onDailyQuotaReset,
  onDailyQuotaUpdate,
  onLoadMore,
  onRefresh,
  onRevokeSessions,
  onRetry,
  onRoleChange,
  onSearch,
  onSortByChange,
  onSortDirChange,
  query,
  searchInput,
  setSearchInput,
  sortBy,
  sortDir,
  total,
  users,
}: {
  defaultCustomDailyGenerationLimit: number;
  hasMore: boolean;
  isError: boolean;
  isLoading: boolean;
  isLoadingMore: boolean;
  onBanChange: (user: AdminUser, isBanned: boolean) => Promise<unknown>;
  onDailyQuotaAdjust: (user: AdminUser, delta: number) => Promise<unknown>;
  onDailyQuotaReset: (user: AdminUser) => Promise<unknown>;
  onDailyQuotaUpdate: (user: AdminUser, limit: number | null) => Promise<unknown>;
  onLoadMore: () => void;
  onRefresh: () => void;
  onRevokeSessions: (user: AdminUser) => Promise<unknown>;
  onRetry: () => void;
  onRoleChange: (user: AdminUser, role: AdminUserRole) => Promise<unknown>;
  onSearch: () => void;
  onSortByChange: (sortBy: AdminUserSortBy) => void;
  onSortDirChange: (sortDir: AdminUserSortDir) => void;
  query: string;
  searchInput: string;
  setSearchInput: Dispatch<SetStateAction<string>>;
  sortBy: AdminUserSortBy;
  sortDir: AdminUserSortDir;
  total: number;
  users: AdminUser[];
}) {
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [menuUser, setMenuUser] = useState<AdminUser | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAdminUserAction | null>(null);
  const [isConfirming, setIsConfirming] = useState(false);

  const closeMenu = () => {
    setMenuAnchor(null);
    setMenuUser(null);
  };

  const openMenu = (event: MouseEvent<HTMLElement>, user: AdminUser) => {
    setMenuAnchor(event.currentTarget);
    setMenuUser(user);
  };

  const requestAction = (action: PendingAdminUserAction) => {
    closeMenu();
    setPendingAction(action);
  };

  const updateDailyQuota = async (user: AdminUser, limit: number | null) => {
    closeMenu();
    await onDailyQuotaUpdate(user, limit);
  };

  const promptDailyQuota = async (user: AdminUser) => {
    const raw = window.prompt("自定义每日额度", String(user.daily_generation_limit));
    if (raw === null) {
      closeMenu();
      return;
    }
    const nextLimit = Number(raw);
    if (!Number.isInteger(nextLimit) || nextLimit < 0) {
      closeMenu();
      return;
    }
    await updateDailyQuota(user, nextLimit);
  };

  const resetDailyQuota = async (user: AdminUser) => {
    closeMenu();
    await onDailyQuotaReset(user);
  };

  const promptDailyQuotaAdjust = async (user: AdminUser, direction: 1 | -1) => {
    const raw = window.prompt(direction > 0 ? "增加多少额度？" : "减少多少额度？", "1");
    if (raw === null) {
      closeMenu();
      return;
    }
    const amount = Number(raw);
    if (!Number.isInteger(amount) || amount <= 0) {
      closeMenu();
      return;
    }
    closeMenu();
    await onDailyQuotaAdjust(user, amount * direction);
  };

  const cancelAction = () => {
    if (isConfirming) return;
    setPendingAction(null);
  };

  const confirmAction = async () => {
    if (!pendingAction) return;
    setIsConfirming(true);
    try {
      if (pendingAction.type === "role") {
        await onRoleChange(pendingAction.user, pendingAction.role);
      } else if (pendingAction.type === "ban") {
        await onBanChange(pendingAction.user, pendingAction.isBanned);
      } else {
        await onRevokeSessions(pendingAction.user);
      }
      setPendingAction(null);
    } finally {
      setIsConfirming(false);
    }
  };

  return (
    <>
      <Paper className="audit-dashboard-card audit-job-table-card" data-testid="admin-user-management" radius={0}>
        <Group className="audit-table-title" justify="space-between" wrap="wrap">
          <Group gap="sm" wrap="wrap">
            <Title order={2} size="h4">
              用户管理
            </Title>
            <Button leftSection={<IconRefresh size={15} />} loading={isLoading} onClick={onRefresh} variant="subtle">
              刷新
            </Button>
          </Group>
          <Group gap="sm" wrap="wrap">
            <SegmentedControl<AdminUserSortBy>
              aria-label="用户排序"
              data={[
                { label: "创建时间", value: "created_at" },
                { label: "生成数", value: "generation_count" },
                { label: "剩余额度", value: "remaining_quota" },
              ]}
              onChange={onSortByChange}
              value={sortBy}
            />
            <SegmentedControl<AdminUserSortDir>
              aria-label="排序方向"
              data={[
                { label: "降序", value: "desc" },
                { label: "升序", value: "asc" },
              ]}
              onChange={onSortDirChange}
              value={sortDir}
            />
            <TextInput
              label="搜索用户"
              onChange={(event: ChangeEvent<HTMLInputElement>) => setSearchInput(event.currentTarget.value)}
              onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
                if (event.key === "Enter") onSearch();
              }}
              placeholder="邮箱"
              value={searchInput}
              w={260}
            />
            <Button leftSection={<IconRefresh size={15} />} onClick={onSearch} variant="filled">
              搜索
            </Button>
          </Group>
        </Group>
        <Box className="audit-table-wrap">
          <table className="audit-job-table">
            <thead>
              <tr>
                <th>邮箱</th>
                <th>角色</th>
                <th>状态</th>
                <th>任务数</th>
                <th>调用数</th>
                <th>生成数</th>
                <th>每日额度</th>
                <th>最近登录</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {isError ? (
                <tr>
                  <td colSpan={10}>
                    <Stack align="center" gap="sm" py="md">
                      <Text>用户列表加载失败，请重试。</Text>
                      <Button leftSection={<IconRefresh size={15} />} onClick={onRetry} size="xs" variant="subtle">
                        重试用户列表
                      </Button>
                    </Stack>
                  </td>
                </tr>
              ) : users.length ? (
                users.map((user) => (
                  <tr key={user.id}>
                    <td>{user.email}</td>
                    <td>
                      <Badge color={user.role === "admin" ? "cyan" : "gray"} variant="light">
                        {roleText(user.role)}
                      </Badge>
                    </td>
                    <td>
                      <span className={`audit-status-label ${user.is_banned ? "is-danger" : "is-success"}`}>
                        {banText(user)}
                      </span>
                    </td>
                    <td>{formatInteger(user.task_count)}</td>
                    <td>{formatInteger(user.call_count)}</td>
                    <td>{formatInteger(user.generation_count)}</td>
                    <td>
                      <Stack gap={0}>
                        <Text size="sm">
                          {formatInteger(user.daily_generation_used)}/{formatInteger(user.daily_generation_limit)}
                        </Text>
                        <Text c="dimmed" size="xs">
                          {user.daily_generation_limit_override === null ? "全局额度" : "自定义额度"}
                        </Text>
                      </Stack>
                    </td>
                    <td>{formatDateTime(user.last_login_at)}</td>
                    <td>{formatDateTime(user.created_at)}</td>
                    <td>
                      <ActionIcon
                        aria-label={`用户操作 ${user.email}`}
                        data-testid={`admin-user-actions-${user.id}`}
                        onClick={(event: MouseEvent<HTMLElement>) => openMenu(event, user)}
                        size="sm"
                        variant="subtle"
                      >
                        <IconDotsVertical size={16} />
                      </ActionIcon>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={10}>{query ? "未找到匹配用户。" : "暂无用户记录。"}</td>
                </tr>
              )}
            </tbody>
          </table>
        </Box>
        <Group justify="space-between" mt="md" wrap="wrap">
          <Text c="dimmed" size="sm">
            已加载 {formatInteger(users.length)} / {formatInteger(total)}
          </Text>
          {hasMore ? (
            <Button loading={isLoadingMore} onClick={onLoadMore} variant="subtle">
              加载更多
            </Button>
          ) : null}
        </Group>
      </Paper>

      <Menu anchorEl={menuAnchor} onClose={closeMenu} open={Boolean(menuAnchor && menuUser)}>
        {menuUser ? (
          <>
            {menuUser.role !== "admin" ? (
              <MenuItem onClick={() => requestAction({ role: "admin", type: "role", user: menuUser })}>
                设为管理员
              </MenuItem>
            ) : null}
            {menuUser.role !== "user" ? (
              <MenuItem onClick={() => requestAction({ role: "user", type: "role", user: menuUser })}>
                设为用户
              </MenuItem>
            ) : null}
            {!menuUser.is_banned ? (
              <MenuItem onClick={() => requestAction({ isBanned: true, type: "ban", user: menuUser })}>封禁</MenuItem>
            ) : null}
            {menuUser.is_banned ? (
              <MenuItem onClick={() => requestAction({ isBanned: false, type: "ban", user: menuUser })}>解封</MenuItem>
            ) : null}
            {menuUser.daily_generation_limit_override === null ? (
              <MenuItem
                data-testid="admin-user-enable-custom-daily-quota"
                onClick={() => void updateDailyQuota(menuUser, defaultCustomDailyGenerationLimit)}
              >
                启用自定义额度
              </MenuItem>
            ) : (
              <MenuItem
                data-testid="admin-user-clear-custom-daily-quota"
                onClick={() => void updateDailyQuota(menuUser, null)}
              >
                清除自定义额度
              </MenuItem>
            )}
            <MenuItem data-testid="admin-user-change-daily-quota" onClick={() => void promptDailyQuota(menuUser)}>
              修改自定义额度
            </MenuItem>
            <MenuItem data-testid="admin-user-reset-daily-quota" onClick={() => void resetDailyQuota(menuUser)}>
              重置今日额度
            </MenuItem>
            <MenuItem data-testid="admin-user-add-daily-quota" onClick={() => void promptDailyQuotaAdjust(menuUser, 1)}>
              增加额度
            </MenuItem>
            <MenuItem data-testid="admin-user-subtract-daily-quota" onClick={() => void promptDailyQuotaAdjust(menuUser, -1)}>
              减少额度
            </MenuItem>
            <MenuItem onClick={() => requestAction({ type: "revoke_sessions", user: menuUser })}>强制下线</MenuItem>
          </>
        ) : null}
      </Menu>

      <AdminUserActionConfirmDialog
        action={pendingAction}
        isPending={isConfirming}
        onCancel={cancelAction}
        onConfirm={() => void confirmAction()}
      />
    </>
  );
}

function AdminNoPermissionPanel() {
  return (
    <Container py={{ base: "lg", md: "xl" }} size="sm">
      <Paper className="grunge-card" p={{ base: "lg", md: "xl" }} radius={0}>
        <Stack gap="md">
          <Group gap="sm" wrap="nowrap">
            <Badge color="cyan" leftSection={<IconShieldCheck size={14} />} radius="sm" variant="light">
              Admin
            </Badge>
            <Box>
              <Title c="var(--kb-ink)" order={1} size="h3">
                KigCraft Admin
              </Title>
              <Text c="var(--kb-concrete-grey)" size="sm">
                当前账号没有管理员权限。
              </Text>
            </Box>
          </Group>
          <Alert color="yellow" radius="sm" variant="light">
            无权限访问
          </Alert>
          <Group justify="flex-start">
            <Button component={Link} leftSection={<IconArrowLeft size={16} />} to="/v2" variant="subtle">
              返回工作台
            </Button>
          </Group>
        </Stack>
      </Paper>
    </Container>
  );
}

function AdminDataErrorPanel({ onRetry }: { onRetry: () => void }) {
  return (
    <Container py={{ base: "lg", md: "xl" }} size="sm">
      <Paper className="grunge-card" p={{ base: "lg", md: "xl" }} radius={0}>
        <Stack gap="md">
          <Group gap="sm" wrap="nowrap">
            <Badge color="cyan" leftSection={<IconShieldCheck size={14} />} radius="sm" variant="light">
              Admin
            </Badge>
            <Box>
              <Title c="var(--kb-ink)" order={1} size="h3">
                管理数据暂时不可用
              </Title>
              <Text c="var(--kb-concrete-grey)" size="sm">
                审计汇总加载失败，请稍后重试。
              </Text>
            </Box>
          </Group>
          <Alert color="red" radius="sm" variant="light">
            当前无法获取管理数据，权限状态正常时也可能出现这种情况。
          </Alert>
          <Group justify="flex-start">
            <Button leftSection={<IconRefresh size={16} />} onClick={onRetry} variant="filled">
              重试
            </Button>
            <Button component={Link} leftSection={<IconArrowLeft size={16} />} to="/v2" variant="subtle">
              返回工作台
            </Button>
          </Group>
        </Stack>
      </Paper>
    </Container>
  );
}

export function AdminAuditPage() {
  const { isAdmin, isLoading: isAuthLoading } = useAuth();
  const queryClient = useQueryClient();
  const [draftPolicy, setDraftPolicy] = useState<QuotaPolicy>(defaultPolicy);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [activeProviderPresetId, setActiveProviderPresetId] = useState<ProviderPreset["id"]>("codex_full");
  const [userSearchInput, setUserSearchInput] = useState("");
  const [userQuery, setUserQuery] = useState("");
  const [userSortBy, setUserSortBy] = useState<AdminUserSortBy>("created_at");
  const [userSortDir, setUserSortDir] = useState<AdminUserSortDir>("desc");
  const [codexDeviceSession, setCodexDeviceSession] = useState<CodexDeviceAuthSession | null>(null);
  const [activeTab, setActiveTab] = useState<AuditAdminTab>(() =>
    typeof window === "undefined" ? "overview" : auditTabFromHash(window.location.hash),
  );
  const summaryQuery = useQuery({
    enabled: isAdmin,
    queryKey: ["audit-summary"],
    queryFn: getAuditSummary,
    retry: false,
  });
  const summary = summaryQuery.data;
  const providerPresetsQuery = useQuery({
    enabled: isAdmin,
    queryKey: ["provider-presets"],
    queryFn: getProviderPresets,
    retry: false,
  });
  const providerUsageQuery = useQuery({
    enabled: isAdmin,
    queryKey: ["provider-usage"],
    queryFn: getProviderUsage,
    retry: false,
  });
  const generationJobsQuery = useInfiniteQuery({
    enabled: isAdmin && activeTab === "jobs",
    queryKey: ["audit-generation-jobs"],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => getAuditGenerationJobsPage({ cursor: pageParam, limit: 20 }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const adminUsersQuery = useInfiniteQuery({
    enabled: isAdmin && activeTab === "accounts",
    queryKey: ["admin-users", userQuery, userSortBy, userSortDir],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      listAdminUsersPage({
        limit: 25,
        offset: Number(pageParam),
        query: userQuery,
        sortBy: userSortBy,
        sortDir: userSortDir,
      }),
    getNextPageParam: (page) => (page.has_more ? page.offset + page.limit : undefined),
    retry: false,
  });
  const codexProfilesQuery = useQuery({
    enabled: isAdmin && activeTab === "quota",
    queryKey: ["codex-profiles"],
    queryFn: listCodexProfiles,
    retry: false,
  });
  const generationJobs = useMemo(
    () => generationJobsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [generationJobsQuery.data],
  );
  const adminUsers = useMemo(
    () => adminUsersQuery.data?.pages.flatMap((page) => page.users) ?? [],
    [adminUsersQuery.data],
  );
  const adminUsersTotal = adminUsersQuery.data?.pages[0]?.total ?? 0;
  const tokenUsage = getTokenUsage(summary);
  const imageUsage = getImageUsage(summary);
  const runningJobs = summary?.running_job_count ?? 0;
  const failedJobs = summary?.job_counts?.failed ?? 0;
  const highQueueLoad = (summary?.queue_length ?? 0) > 6;
  const trendPoints = useMemo(() => buildTrendPointsFromSummary(summary), [summary]);

  const refreshAudit = () => {
    void queryClient.invalidateQueries({ queryKey: ["audit-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["audit-generation-jobs"] });
    void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    void queryClient.invalidateQueries({ queryKey: ["provider-presets"] });
    void queryClient.invalidateQueries({ queryKey: ["provider-usage"] });
  };

  const selectAuditTab = (tab: AuditAdminTab) => {
    setActiveTab(tab);
    if (typeof window !== "undefined") {
      window.history.replaceState(null, "", tabHashById[tab]);
    }
  };

  const saveMutation = useMutation({
    mutationFn: updateQuotaPolicy,
    onSuccess: (policy) => {
      setDraftPolicy(policy);
      setSaveMessage("配额策略已保存。");
      queryClient.setQueryData(["audit-summary"], summary ? { ...summary, quota_policy: policy } : summary);
      void queryClient.invalidateQueries({ queryKey: ["audit-summary"] });
    },
  });

  const providerPresetMutation = useMutation({
    mutationFn: setActiveProviderPreset,
    onSuccess: (activePreset) => {
      setActiveProviderPresetId(activePreset.preset_id);
      void queryClient.invalidateQueries({ queryKey: ["provider-presets"] });
      void queryClient.invalidateQueries({ queryKey: ["provider-usage"] });
    },
  });

  const roleMutation = useMutation({
    mutationFn: ({ role, userId }: { role: AdminUserRole; userId: string }) =>
      updateAdminUserRole(userId, role),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  const banMutation = useMutation({
    mutationFn: ({ isBanned, userId }: { isBanned: boolean; userId: string }) =>
      updateAdminUserBan(userId, isBanned),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  const revokeSessionsMutation = useMutation({
    mutationFn: revokeAdminUserSessions,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  const resetDailyQuotaMutation = useMutation({
    mutationFn: resetDailyGenerationQuota,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  const resetUserDailyQuotaMutation = useMutation({
    mutationFn: resetAdminUserDailyGenerationQuota,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  const dailyQuotaLimitMutation = useMutation({
    mutationFn: ({ limit, userId }: { limit: number | null; userId: string }) =>
      updateAdminUserDailyGenerationLimit(userId, limit),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const dailyQuotaAdjustMutation = useMutation({
    mutationFn: ({ delta, userId }: { delta: number; userId: string }) =>
      adjustAdminUserDailyGenerationQuota(userId, delta),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const activateCodexProfileMutation = useMutation({
    mutationFn: activateCodexProfile,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["codex-profiles"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-summary"] });
    },
  });
  const renameCodexProfileMutation = useMutation({
    mutationFn: renameCodexProfile,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["codex-profiles"] });
    },
  });
  const deleteCodexProfileMutation = useMutation({
    mutationFn: deleteCodexProfile,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["codex-profiles"] });
      void queryClient.invalidateQueries({ queryKey: ["audit-summary"] });
    },
  });
  const codexUpdateStatusQuery = useQuery({
    enabled: isAdmin && activeTab === "quota",
    queryKey: ["codex-update-status"],
    queryFn: getCodexUpdateStatus,
    retry: false,
  });
  const startCodexDeviceAuthMutation = useMutation({
    mutationFn: startCodexDeviceAuth,
    onSuccess: (session) => setCodexDeviceSession(session),
  });
  const refreshCodexDeviceAuthMutation = useMutation({
    mutationFn: ({ profileId, sessionId }: { profileId: string; sessionId: string }) =>
      getCodexProfileDeviceAuth(profileId, sessionId),
    onSuccess: (session) => {
      if (session.status === "succeeded") {
        void queryClient.invalidateQueries({ queryKey: ["codex-profiles"] });
      }
    },
  });
  const cancelCodexDeviceAuthMutation = useMutation({
    mutationFn: ({ profileId, sessionId }: { profileId: string; sessionId: string }) =>
      cancelCodexProfileDeviceAuth(profileId, sessionId),
    onSuccess: (session) => setCodexDeviceSession(session),
  });

  useEffect(() => {
    if (summary?.quota_policy) setDraftPolicy(summary.quota_policy);
  }, [summary?.quota_policy]);

  useEffect(() => {
    if (providerPresetsQuery.data) setActiveProviderPresetId(providerPresetsQuery.data.active.preset_id);
  }, [providerPresetsQuery.data]);

  useEffect(() => {
    const handleHashChange = () => setActiveTab(auditTabFromHash(window.location.hash));
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  const renderKpiGrid = () => (
    <SimpleGrid className="audit-kpi-grid" cols={{ base: 1, md: 2, lg: 4 }} spacing="md">
      <MetricCard
        icon={<IconFileText size={30} />}
        label="总生成任务"
        trendValues={trendPoints.map((point) => point.calls)}
        value={formatInteger(summary?.total_calls)}
      />
      <MetricCard
        icon={<IconCheck size={30} />}
        label="成功率"
        trendValues={trendPoints.map((point) => (point.calls > 0 ? point.succeeded / point.calls : 0))}
        value={formatPercent(summary?.success_rate)}
      />
      <MetricCard
        icon={<IconStack2 size={30} />}
        label="队列负载"
        trendValues={trendPoints.map((point) => point.active)}
        value={`${summary?.queue_length ?? 0}`}
      />
      <MetricCard
        icon={<IconDatabase size={30} />}
        label="令牌用量"
        trendValues={trendPoints.map((point) => point.tokens)}
        value={formatCompact(tokenUsage.total_tokens)}
      />
    </SimpleGrid>
  );

  const renderUsageDetails = () => (
    <UsageDetailPanels
      failedJobs={failedJobs}
      imageUsage={imageUsage}
      latestJobUpdatedAt={summary?.latest_job_updated_at}
      onRefresh={refreshAudit}
      tokenUsage={tokenUsage}
      windowHours={draftPolicy.window_hours}
    />
  );

  const renderUsageTab = (id: string) => (
    <>
      <UsageTrendChart id={id} points={trendPoints} primaryLabel="Token 消耗" secondaryKey="images" secondaryLabel="图片产出" title="趋势" />
      {renderUsageDetails()}
    </>
  );

  const renderJobsTab = (id = "audit-jobs") => (
    <RecentJobsTable
      detailMode
      hasMore={generationJobsQuery.hasNextPage}
      id={id}
      jobs={generationJobs}
      loadingMore={generationJobsQuery.isFetchingNextPage}
      onLoadMore={() => {
        void generationJobsQuery.fetchNextPage();
      }}
      onRefresh={refreshAudit}
    />
  );

  const renderQuotaTab = (id = "audit-quota") => (
    <>
      <QuotaEditor
        draftPolicy={draftPolicy}
        id={id}
        onResetDailyQuota={() => resetDailyQuotaMutation.mutate()}
        resetDailyQuotaPending={resetDailyQuotaMutation.isPending}
        saveMessage={saveMessage}
        saveMutation={saveMutation}
        setDraftPolicy={setDraftPolicy}
      />
      <CodexProfilesPanel
        updateStatus={codexUpdateStatusQuery.data}
        deviceSession={codexDeviceSession}
        isDeleting={deleteCodexProfileMutation.isPending}
        isLoading={codexProfilesQuery.isFetching}
        isStarting={startCodexDeviceAuthMutation.isPending}
        onActivate={(profileId) => activateCodexProfileMutation.mutateAsync(profileId)}
        onCancelDeviceAuth={(profileId, sessionId) =>
          cancelCodexDeviceAuthMutation.mutateAsync({ profileId, sessionId })}
        onDelete={(profileId) => deleteCodexProfileMutation.mutateAsync(profileId)}
        onRename={(profileId, name) => renameCodexProfileMutation.mutateAsync({ profileId, name })}
        onRefresh={() => {
          void codexProfilesQuery.refetch();
          void codexUpdateStatusQuery.refetch();
        }}
        onRefreshDeviceAuth={(profileId, sessionId) =>
          refreshCodexDeviceAuthMutation.mutateAsync({ profileId, sessionId })}
        onStartDeviceAuth={() => startCodexDeviceAuthMutation.mutateAsync()}
        profiles={codexProfilesQuery.data ?? []}
      />
    </>
  );

  const submitUserSearch = () => setUserQuery(userSearchInput.trim());

  const renderOverviewTab = () => (
    <>
      {renderKpiGrid()}
      <Box className="audit-dashboard-grid">
        <UsageTrendChart id="audit-overview-usage" points={trendPoints} />
        <Stack className="audit-dashboard-side" gap="md">
          <QueueHealthPanel
            activeJobs={runningJobs}
            failureRate={summary?.failure_rate ?? 0}
            parallelLimit={draftPolicy.parallel_generation_limit}
            parallelUsed={summary?.parallel_slots_used ?? 0}
            queueLength={summary?.queue_length ?? 0}
          />
          <CodexQuotaPanel codexQuota={summary?.codex_quota} />
          <CompactQuotaPanel draftPolicy={draftPolicy} />
        </Stack>
      </Box>
      {providerPresetsQuery.data ? (
        <ProviderPresetAndUsagePanel
          activePresetId={activeProviderPresetId}
          onPresetChange={setActiveProviderPresetId}
          onSave={() => providerPresetMutation.mutate(activeProviderPresetId)}
          presets={providerPresetsQuery.data.presets}
          saving={providerPresetMutation.isPending}
          usage={providerUsageQuery.data ?? []}
        />
      ) : null}
    </>
  );

  const renderAccountsTab = () => (
    <AdminUsersPanel
      defaultCustomDailyGenerationLimit={draftPolicy.daily_generation_custom_limit_default}
      hasMore={adminUsersQuery.hasNextPage}
      isError={adminUsersQuery.isError}
      isLoading={adminUsersQuery.isFetching}
      isLoadingMore={adminUsersQuery.isFetchingNextPage}
      onBanChange={(user, isBanned) => banMutation.mutateAsync({ isBanned, userId: user.id })}
      onDailyQuotaAdjust={(user, delta) => dailyQuotaAdjustMutation.mutateAsync({ delta, userId: user.id })}
      onDailyQuotaReset={(user) => resetUserDailyQuotaMutation.mutateAsync(user.id)}
      onDailyQuotaUpdate={(user, limit) => dailyQuotaLimitMutation.mutateAsync({ limit, userId: user.id })}
      onLoadMore={() => {
        void adminUsersQuery.fetchNextPage();
      }}
      onRefresh={() => void queryClient.invalidateQueries({ queryKey: ["admin-users"] })}
      onRevokeSessions={(user) => revokeSessionsMutation.mutateAsync(user.id)}
      onRetry={() => void adminUsersQuery.refetch()}
      onRoleChange={(user, role) => roleMutation.mutateAsync({ role, userId: user.id })}
      onSearch={submitUserSearch}
      onSortByChange={setUserSortBy}
      onSortDirChange={setUserSortDir}
      query={userQuery}
      searchInput={userSearchInput}
      setSearchInput={setUserSearchInput}
      sortBy={userSortBy}
      sortDir={userSortDir}
      total={adminUsersTotal}
      users={adminUsers}
    />
  );

  return (
    <AppShell header={{ height: 96 }} padding={0}>
      <AppShell.Main className="audit-console-page" style={{ minHeight: "100vh" }}>
        {isAuthLoading ? (
          <Container py="xl" size="sm">
            <Text c="var(--kb-concrete-grey)">正在检查管理员权限...</Text>
          </Container>
        ) : !isAdmin ? (
          <AdminNoPermissionPanel />
        ) : summaryQuery.isLoading ? (
          <Container py="xl" size="sm">
            <Text c="var(--kb-concrete-grey)">正在加载审计数据...</Text>
          </Container>
        ) : summaryQuery.isError ? (
          <AdminDataErrorPanel onRetry={() => void summaryQuery.refetch()} />
        ) : (
          <>
            <AdminShellHeader
              connectionState={summaryQuery.isFetching ? "loading" : "connected"}
              onRefresh={refreshAudit}
              queueLength={summary?.queue_length ?? 0}
              runningJobs={runningJobs}
            />
            <Box className="audit-console-layout">
              <AdminSidebar activeTab={activeTab} onRefresh={refreshAudit} onSelectTab={selectAuditTab} />
              <Box className="audit-console-main" data-testid="audit-desktop-tab-panel">
                {highQueueLoad ? (
                  <Alert color="yellow" radius="sm" variant="light">
                    服务器负载较高，当前排队 {summary?.queue_length ?? 0} 个任务。建议稍后再试。
                  </Alert>
                ) : null}
                <AdminTabHeading activeTab={activeTab} />
                {activeTab === "overview" ? renderOverviewTab() : null}
                {activeTab === "usage" ? renderUsageTab("audit-usage") : null}
                {activeTab === "jobs" ? renderJobsTab() : null}
                {activeTab === "accounts" ? renderAccountsTab() : null}
                {activeTab === "quota" ? renderQuotaTab() : null}
              </Box>
            </Box>
            <MobileBottomNav activeTab={activeTab} onSelectTab={selectAuditTab} />
          </>
        )}
      </AppShell.Main>
    </AppShell>
  );
}
