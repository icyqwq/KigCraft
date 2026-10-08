import { Badge, Box, Group, Paper, SimpleGrid, Stack, Text, Title } from "../ui/mui";
import type { ReactNode } from "react";
import type { AuditGenerationJob, AuditSummary } from "../api/client";
import { EChart } from "../components/charts/EChart";
import { buildSingleTrendOption } from "../components/charts/adminChartOptions";

type TrendPoint = {
  label: string;
  value: number;
};

function jobTimestamp(job: AuditGenerationJob) {
  return job.created_at ?? job.updated_at ?? null;
}

function formatDayKey(value: string) {
  return value.slice(0, 10);
}

function formatShortDayLabel(dayKey: string) {
  const [, month, day] = dayKey.split("-");
  if (!month || !day) return dayKey;
  return `${month}-${day}`;
}

export function buildDailyCallTrend(jobs: AuditGenerationJob[]): TrendPoint[] {
  const buckets = new Map<string, number>();
  for (const job of jobs) {
    const timestamp = jobTimestamp(job);
    if (!timestamp) continue;
    const dayKey = formatDayKey(timestamp);
    buckets.set(dayKey, (buckets.get(dayKey) ?? 0) + 1);
  }

  return [...buckets.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([dayKey, value]) => ({
      label: formatShortDayLabel(dayKey),
      value,
    }));
}

export function buildCumulativeUserTrend(jobs: AuditGenerationJob[]): TrendPoint[] {
  const dayUsers = new Map<string, Set<string>>();
  for (const job of jobs) {
    const timestamp = jobTimestamp(job);
    const sessionId = job.character_session_id?.trim();
    if (!timestamp || !sessionId) continue;
    const dayKey = formatDayKey(timestamp);
    const users = dayUsers.get(dayKey) ?? new Set<string>();
    users.add(sessionId);
    dayUsers.set(dayKey, users);
  }

  const seenUsers = new Set<string>();
  return [...dayUsers.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([dayKey, users]) => {
      for (const sessionId of users) seenUsers.add(sessionId);
      return {
        label: formatShortDayLabel(dayKey),
        value: seenUsers.size,
      };
    });
}

function TrendLineChart({
  color,
  emptyText,
  points,
  testId,
  totalHint,
  yAxisHint,
}: {
  color: string;
  emptyText: string;
  points: TrendPoint[];
  testId: string;
  totalHint?: string;
  yAxisHint: string;
}) {
  if (!points.length) {
    return (
      <Text c="var(--kb-concrete-grey)" data-testid={testId} size="sm">
        {emptyText}
      </Text>
    );
  }

  const latestValue = points[points.length - 1]?.value ?? 0;
  const option = buildSingleTrendOption({
    color,
    labels: points.map((point) => point.label),
    name: yAxisHint,
    values: points.map((point) => point.value),
  });

  return (
    <Stack data-testid={testId} gap="xs">
      {totalHint ? (
        <Text c="var(--kb-concrete-grey)" size="xs">
          {totalHint}
        </Text>
      ) : null}
      <EChart ariaLabel={yAxisHint} className="audit-trend-chart" option={option} />
      <Text c="var(--kb-ink)" fw={900} size="sm">
        {yAxisHint}: {latestValue.toLocaleString("en-US")}
      </Text>
    </Stack>
  );
}

function ChartPanel({
  children,
  subtitle,
  testId,
  title,
}: {
  children: ReactNode;
  subtitle: string;
  testId: string;
  title: string;
}) {
  return (
    <Paper className="audit-chart-card" data-testid={testId} p="md" radius={0}>
      <Stack gap="sm">
        <Box>
          <Title c="var(--kb-ink)" order={3} size="h5">
            {title}
          </Title>
          <Text c="var(--kb-concrete-grey)" size="xs">
            {subtitle}
          </Text>
        </Box>
        {children}
      </Stack>
    </Paper>
  );
}

export function AuditOverviewCharts({ jobs, summary }: { jobs: AuditGenerationJob[]; summary: AuditSummary }) {
  const userTrend = buildCumulativeUserTrend(jobs);
  const callTrend = buildDailyCallTrend(jobs);

  return (
    <Paper className="grunge-card audit-section audit-chart-section" data-testid="audit-overview-charts" p={{ base: "md", md: "lg" }} radius={0}>
      <Stack gap="md">
        <Group justify="space-between">
          <Box>
            <Title c="var(--kb-ink)" order={2} size="h3">
              用量趋势
            </Title>
            <Text c="var(--kb-concrete-grey)" size="sm">
              按日期展示累计用户与每日调用次数。
            </Text>
          </Box>
          <Group gap="xs">
            <Badge color="teal" variant="light">
              累计用户
            </Badge>
            <Badge color="yellow" variant="light">
              每日调用
            </Badge>
          </Group>
        </Group>

        <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
          <ChartPanel subtitle="按任务创建日期累计独立 session 数量。" testId="audit-user-chart-panel" title="用户趋势">
            <TrendLineChart
              color="#0d9488"
              emptyText="暂无用户趋势数据。"
              points={userTrend}
              testId="audit-user-chart"
              totalHint={`当前总用户 ${summary.total_users.toLocaleString("en-US")} / 活跃 ${summary.active_users.toLocaleString("en-US")}`}
              yAxisHint="累计用户"
            />
          </ChartPanel>

          <ChartPanel subtitle="按任务创建日期统计每日调用次数。" testId="audit-call-chart-panel" title="调用趋势">
            <TrendLineChart
              color="#c9552f"
              emptyText="暂无调用趋势数据。"
              points={callTrend}
              testId="audit-call-chart"
              totalHint={`累计 ${summary.total_calls.toLocaleString("en-US")} 次 / 成功率 ${(summary.success_rate * 100).toFixed(1)}%`}
              yAxisHint="每日调用"
            />
          </ChartPanel>
        </SimpleGrid>
      </Stack>
    </Paper>
  );
}
