import { describe, expect, it } from "vitest";
import { estimateGenerationDurationMs } from "./generationTiming";

describe("generationTiming", () => {
  it("falls back to the one-image benchmark when local history is not enough", () => {
    expect(estimateGenerationDurationMs([], { expectedOutputCount: 1, generationMode: "front_design" })).toBe(
      6 * 60_000,
    );
  });

  it("uses a shorter benchmark for detail analysis when local history is not enough", () => {
    expect(estimateGenerationDurationMs([], { generationMode: "detail_analysis" })).toBe(90_000);
  });

  it("treats local front revision as a one-image generation", () => {
    expect(estimateGenerationDurationMs([], { expectedOutputCount: 1, generationMode: "front_local_revision" })).toBe(
      6 * 60_000,
    );
  });

  it("uses legacy history samples that do not have mode metadata", () => {
    expect(
      estimateGenerationDurationMs(
        [
          { durationMs: 300_000, finishedAt: "2026-06-28T10:00:00Z", jobId: "job-a" },
          { durationMs: 360_000, finishedAt: "2026-06-28T11:00:00Z", jobId: "job-b" },
          { durationMs: 420_000, finishedAt: "2026-06-28T12:00:00Z", jobId: "job-c" },
        ],
        { expectedOutputCount: 1, generationMode: "front_design" },
      ),
    ).toBe(360_000);
  });

  it("prefers matching local history over the benchmark", () => {
    expect(
      estimateGenerationDurationMs(
        [
          {
            durationMs: 120_000,
            expectedOutputCount: 1,
            finishedAt: "2026-06-28T10:00:00Z",
            generationMode: "front_design",
            jobId: "job-a",
          },
          {
            durationMs: 180_000,
            expectedOutputCount: 1,
            finishedAt: "2026-06-28T11:00:00Z",
            generationMode: "front_design",
            jobId: "job-b",
          },
          {
            durationMs: 240_000,
            expectedOutputCount: 1,
            finishedAt: "2026-06-28T12:00:00Z",
            generationMode: "front_design",
            jobId: "job-c",
          },
        ],
        { expectedOutputCount: 1, generationMode: "front_design" },
      ),
    ).toBe(180_000);
  });
});
