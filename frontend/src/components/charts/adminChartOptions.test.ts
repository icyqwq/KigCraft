import { describe, expect, it } from "vitest";
import {
  buildDualTrendOption,
  buildSingleTrendOption,
  buildSparklineOption,
} from "./adminChartOptions";

describe("admin chart options", () => {
  it("builds a dual-axis trend with an axis tooltip and both series", () => {
    const option = buildDualTrendOption({
      labels: ["07-12", "07-13"],
      primary: { color: "#c9552f", data: [1200, 2400], name: "Token 消耗" },
      secondary: { color: "#191f23", data: [2, 4], name: "图片产出" },
    });

    expect(option.tooltip).toMatchObject({ trigger: "axis" });
    expect(option.xAxis).toMatchObject({ data: ["07-12", "07-13"], type: "category" });
    expect(option.yAxis).toHaveLength(2);
    expect(option.series).toMatchObject([
      { data: [1200, 2400], name: "Token 消耗", type: "line", yAxisIndex: 0 },
      { data: [2, 4], name: "图片产出", type: "line", yAxisIndex: 1 },
    ]);
  });

  it("builds a single-axis area trend with visible axes", () => {
    const option = buildSingleTrendOption({
      color: "#1677ff",
      labels: ["07-12", "07-13"],
      name: "调用次数",
      values: [3, 8],
    });

    expect(option.tooltip).toMatchObject({ trigger: "axis" });
    expect(option.xAxis).toMatchObject({ show: true, type: "category" });
    expect(option.yAxis).toMatchObject({ show: true, type: "value" });
    expect(option.series).toMatchObject([{ areaStyle: expect.any(Object), data: [3, 8], type: "line" }]);
  });

  it("builds a compact sparkline with hidden axes and an item tooltip", () => {
    const option = buildSparklineOption({ color: "#c9552f", name: "Token", values: [1, 5, 3] });

    expect(option.tooltip).toMatchObject({ trigger: "item" });
    expect(option.xAxis).toMatchObject({ show: false });
    expect(option.yAxis).toMatchObject({ show: false });
    expect(option.series).toMatchObject([{ data: [1, 5, 3], showSymbol: false, type: "line" }]);
  });
});
