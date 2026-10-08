import type { EChartsCoreOption } from "echarts/core";

type TrendSeriesInput = {
  color: string;
  data: number[];
  name: string;
};

type DualTrendOptionInput = {
  labels: string[];
  primary: TrendSeriesInput;
  secondary: TrendSeriesInput;
  tertiary?: TrendSeriesInput;
};

type SingleTrendOptionInput = {
  color: string;
  labels: string[];
  name: string;
  values: number[];
};

type SparklineOptionInput = {
  color: string;
  labels?: string[];
  name: string;
  values: number[];
};

function compactNumber(value: number) {
  return Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function preciseNumber(value: unknown) {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : String(value ?? "-");
}

const axisStyle = {
  axisLabel: { color: "#6d675d", fontSize: 11 },
  axisLine: { lineStyle: { color: "#191f23", width: 2 }, show: true },
  axisTick: { lineStyle: { color: "#191f23" }, show: true },
};

export function buildDualTrendOption({ labels, primary, secondary, tertiary }: DualTrendOptionInput) {
  const series = [
    {
      data: primary.data,
      emphasis: { focus: "series" },
      lineStyle: { width: 3 },
      name: primary.name,
      showSymbol: true,
      smooth: 0.22,
      symbol: "circle",
      symbolSize: 7,
      type: "line",
      valueFormatter: preciseNumber,
      yAxisIndex: 0,
    },
    {
      data: secondary.data,
      emphasis: { focus: "series" },
      lineStyle: { width: 3 },
      name: secondary.name,
      showSymbol: true,
      smooth: 0.22,
      symbol: "circle",
      symbolSize: 7,
      type: "line",
      valueFormatter: preciseNumber,
      yAxisIndex: 1,
    },
    ...(tertiary
      ? [
          {
            data: tertiary.data,
            emphasis: { focus: "series" },
            lineStyle: { type: "dashed", width: 3 },
            name: tertiary.name,
            showSymbol: true,
            smooth: 0.22,
            symbol: "diamond",
            symbolSize: 7,
            type: "line",
            valueFormatter: preciseNumber,
            yAxisIndex: 1,
          },
        ]
      : []),
  ];
  return {
    animation: false,
    aria: { enabled: true },
    color: [primary.color, secondary.color, ...(tertiary ? [tertiary.color] : [])],
    grid: { bottom: 42, containLabel: false, left: 58, right: 58, top: 48 },
    legend: {
      data: [primary.name, secondary.name, ...(tertiary ? [tertiary.name] : [])],
      itemHeight: 8,
      itemWidth: 18,
      textStyle: { color: "#191f23", fontSize: 12, fontWeight: 700 },
      top: 8,
    },
    series,
    tooltip: {
      axisPointer: { lineStyle: { color: "#191f23", type: "dashed" }, type: "line" },
      backgroundColor: "#fff4dd",
      borderColor: "#191f23",
      borderWidth: 2,
      confine: true,
      textStyle: { color: "#191f23" },
      trigger: "axis",
    },
    xAxis: {
      ...axisStyle,
      axisLabel: { ...axisStyle.axisLabel, hideOverlap: true, interval: "auto" },
      boundaryGap: false,
      data: labels,
      type: "category",
    },
    yAxis: [
      {
        ...axisStyle,
        axisLabel: { ...axisStyle.axisLabel, formatter: compactNumber },
        min: 0,
        name: primary.name,
        nameGap: 12,
        nameTextStyle: { color: "#6d675d", fontSize: 11 },
        splitLine: { lineStyle: { color: "rgba(25, 31, 35, 0.16)", type: "dashed" }, show: true },
        type: "value",
      },
      {
        ...axisStyle,
        axisLabel: { ...axisStyle.axisLabel, formatter: compactNumber },
        min: 0,
        name: tertiary ? "调用 / 图片" : secondary.name,
        nameGap: 12,
        nameTextStyle: { color: "#6d675d", fontSize: 11 },
        splitLine: { show: false },
        type: "value",
      },
    ],
  } satisfies EChartsCoreOption;
}

export function buildSingleTrendOption({ color, labels, name, values }: SingleTrendOptionInput) {
  return {
    animation: false,
    aria: { enabled: true },
    color: [color],
    grid: { bottom: 42, left: 54, right: 18, top: 18 },
    series: [
      {
        areaStyle: { color, opacity: 0.14 },
        data: values,
        lineStyle: { width: 3 },
        name,
        showSymbol: true,
        smooth: 0.22,
        symbol: "circle",
        symbolSize: 7,
        type: "line",
        valueFormatter: preciseNumber,
      },
    ],
    tooltip: {
      axisPointer: { lineStyle: { color: "#191f23", type: "dashed" }, type: "line" },
      backgroundColor: "#fff4dd",
      borderColor: "#191f23",
      borderWidth: 2,
      confine: true,
      textStyle: { color: "#191f23" },
      trigger: "axis",
    },
    xAxis: {
      ...axisStyle,
      axisLabel: { ...axisStyle.axisLabel, hideOverlap: true, interval: "auto" },
      boundaryGap: false,
      data: labels,
      show: true,
      type: "category",
    },
    yAxis: {
      ...axisStyle,
      axisLabel: { ...axisStyle.axisLabel, formatter: compactNumber },
      min: 0,
      show: true,
      splitLine: { lineStyle: { color: "rgba(25, 31, 35, 0.16)", type: "dashed" }, show: true },
      type: "value",
    },
  } satisfies EChartsCoreOption;
}

export function buildSparklineOption({ color, labels, name, values }: SparklineOptionInput) {
  return {
    animation: false,
    aria: { enabled: true },
    color: [color],
    grid: { bottom: 2, left: 2, right: 2, top: 2 },
    series: [
      {
        data: values,
        lineStyle: { width: 2 },
        name,
        showSymbol: false,
        smooth: 0.25,
        type: "line",
        valueFormatter: preciseNumber,
      },
    ],
    tooltip: {
      backgroundColor: "#fff4dd",
      borderColor: "#191f23",
      borderWidth: 1,
      confine: true,
      textStyle: { color: "#191f23", fontSize: 11 },
      trigger: "item",
    },
    xAxis: { boundaryGap: false, data: labels ?? values.map((_, index) => String(index + 1)), show: false, type: "category" },
    yAxis: { min: "dataMin", show: false, type: "value" },
  } satisfies EChartsCoreOption;
}

type RemainingSeriesInput = { color: string; data: Array<[number, number]>; name: string };

/** Remaining quota (0–100 %) over real time; one line per rate-limit window. */
export function buildRemainingQuotaOption({ series }: { series: RemainingSeriesInput[] }) {
  return {
    animation: false,
    aria: { enabled: true },
    color: series.map((item) => item.color),
    grid: { bottom: 42, left: 52, right: 24, top: 48 },
    legend: {
      data: series.map((item) => item.name),
      itemHeight: 8,
      itemWidth: 18,
      textStyle: { color: "#191f23", fontSize: 12, fontWeight: 700 },
      top: 8,
    },
    series: series.map((item) => ({
      areaStyle: { opacity: 0.08 },
      data: item.data,
      emphasis: { focus: "series" },
      lineStyle: { width: 3 },
      name: item.name,
      showSymbol: item.data.length < 40,
      smooth: false,
      step: "end",
      symbol: "circle",
      symbolSize: 6,
      type: "line",
      valueFormatter: (value: unknown) => (typeof value === "number" ? `${value.toFixed(1)}%` : String(value ?? "-")),
    })),
    tooltip: {
      axisPointer: { lineStyle: { color: "#191f23", type: "dashed" }, type: "line" },
      backgroundColor: "#fff4dd",
      borderColor: "#191f23",
      borderWidth: 2,
      confine: true,
      textStyle: { color: "#191f23" },
      trigger: "axis",
    },
    xAxis: { ...axisStyle, axisLabel: { ...axisStyle.axisLabel, hideOverlap: true }, type: "time" },
    yAxis: {
      ...axisStyle,
      axisLabel: { ...axisStyle.axisLabel, formatter: "{value}%" },
      max: 100,
      min: 0,
      name: "剩余",
      nameGap: 12,
      nameTextStyle: { color: "#6d675d", fontSize: 11 },
      splitLine: { lineStyle: { color: "rgba(25, 31, 35, 0.16)", type: "dashed" }, show: true },
      type: "value",
    },
  } satisfies EChartsCoreOption;
}
