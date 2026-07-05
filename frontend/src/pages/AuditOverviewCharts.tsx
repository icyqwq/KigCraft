import { Box, Paper, SimpleGrid, Stack, Text, Title } from "../ui/mui";
import type { ReactNode } from "react";
import type { AuditGenerationJob, AuditSummary } from "../api/client";

type TrendPoint = {
  label: string;
  value: number;
};

const CHART_WIDTH = 320;
const CHART_HEIGHT = 180;
const CHART_PADDING = { bottom: 28, left: 36, right: 12, top: 12 };

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
      for (const sessionId of users) {
        seenUsers.add(sessionId);
      }
      return {
        label: formatShortDayLabel(dayKey),
        value: seenUsers.size,
      };
    });
}

function buildPlotPoints(points: TrendPoint[]) {
  const plotWidth = CHART_WIDTH - CHART_PADDING.left - CHART_PADDING.right;
  const plotHeight = CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom;
  const maxValue = Math.max(...points.map((point) => point.value), 1);
  const singlePoint = points.length === 1;

  return points.map((point, index) => {
    const xRatio = singlePoint ? 0.5 : index / Math.max(points.length - 1, 1);
    const yRatio = point.value / maxValue;
    return {
      ...point,
      x: CHART_PADDING.left + xRatio * plotWidth,
      y: CHART_PADDING.top + (1 - yRatio) * plotHeight,
    };
  });
}

function buildLinePath(plotPoints: Array<TrendPoint & { x: number; y: number }>) {
  if (!plotPoints.length) return "";
  if (plotPoints.length === 1) {
    const [point] = plotPoints;
    return `M ${CHART_PADDING.left} ${point.y} L ${CHART_WIDTH - CHART_PADDING.right} ${point.y}`;
  }
  return plotPoints.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
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

  const plotPoints = buildPlotPoints(points);
  const linePath = buildLinePath(plotPoints);
  const maxValue = Math.max(...points.map((point) => point.value), 1);
  const latestValue = points[points.length - 1]?.value ?? 0;

  return (
    <Stack data-testid={testId} gap="xs">
      {totalHint ? (
        <Text c="var(--kb-concrete-grey)" size="xs">
          {totalHint}
        </Text>
      ) : null}
      <Box className="audit-trend-chart" style={{ color }}>
        <svg
          aria-label={yAxisHint}
          height={CHART_HEIGHT}
          role="img"
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          width="100%"
        >
          <line
            className="audit-trend-chart-axis"
            x1={CHART_PADDING.left}
            x2={CHART_WIDTH - CHART_PADDING.right}
            y1={CHART_HEIGHT - CHART_PADDING.bottom}
            y2={CHART_HEIGHT - CHART_PADDING.bottom}
          />
          <line
            className="audit-trend-chart-axis"
            x1={CHART_PADDING.left}
            x2={CHART_PADDING.left}
            y1={CHART_PADDING.top}
            y2={CHART_HEIGHT - CHART_PADDING.bottom}
          />
          <text className="audit-trend-chart-label" x={4} y={CHART_PADDING.top + 4}>
            {maxValue}
          </text>
          <text
            className="audit-trend-chart-label"
            x={4}
            y={CHART_HEIGHT - CHART_PADDING.bottom - 2}
          >
            0
          </text>
          <path className="audit-trend-chart-line" d={linePath} fill="none" stroke={color} />
          {plotPoints.map((point) => (
            <g key={`${point.label}-${point.value}`}>
              <circle className="audit-trend-chart-dot" cx={point.x} cy={point.y} fill={color} r={4} />
              <text className="audit-trend-chart-x-label" textAnchor="middle" x={point.x} y={CHART_HEIGHT - 8}>
                {point.label}
              </text>
            </g>
          ))}
        </svg>
      </Box>
      <Text c="var(--kb-ink)" fw={800} size="sm">
        {yAxisHint}：{latestValue.toLocaleString("en-US")}
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

export function AuditOverviewCharts({
  jobs,
  summary,
}: {
  jobs: AuditGenerationJob[];
  summary: AuditSummary;
}) {
  const userTrend = buildCumulativeUserTrend(jobs);
  const callTrend = buildDailyCallTrend(jobs);

  return (
    <Paper className="grunge-card" data-testid="audit-overview-charts" p={{ base: "md", md: "lg" }} radius={0}>
      <Stack gap="md">
        <Box>
          <Title c="var(--kb-ink)" order={2} size="h3">
            统计图表
          </Title>
          <Text c="var(--kb-concrete-grey)" size="sm">
            按日期展示累计用户与每日调用次数的趋势曲线。
          </Text>
        </Box>

        <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
          <ChartPanel
            subtitle="按任务创建日期累计独立 session 数量。"
            testId="audit-user-chart-panel"
            title="用户趋势"
          >
            <TrendLineChart
              color="var(--kb-teal, #0d9488)"
              emptyText="暂无用户趋势数据。"
              points={userTrend}
              testId="audit-user-chart"
              totalHint={`当前总用户 ${summary.total_users.toLocaleString("en-US")} · 活跃 ${summary.active_users.toLocaleString("en-US")}`}
              yAxisHint="累计用户"
            />
          </ChartPanel>

          <ChartPanel
            subtitle="按任务创建日期统计每日调用次数。"
            testId="audit-call-chart-panel"
            title="调用趋势"
          >
            <TrendLineChart
              color="var(--kb-coral)"
              emptyText="暂无调用趋势数据。"
              points={callTrend}
              testId="audit-call-chart"
              totalHint={`累计 ${summary.total_calls.toLocaleString("en-US")} 次 · 成功率 ${(summary.success_rate * 100).toFixed(1)}%`}
              yAxisHint="每日调用"
            />
          </ChartPanel>
        </SimpleGrid>
      </Stack>
    </Paper>
  );
}
