import { ThemeProvider } from "@mui/material/styles";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GenerationJob, JobEvent } from "../../api/client";
import { kigTheme } from "../../app/theme";
import i18n from "../../i18n";
import { DEFAULT_LOCALE } from "../../i18n/locales";
import { GenerationReview } from "./GenerationReview";
import { GENERATION_DURATION_HISTORY_STORAGE_KEY } from "./generationTiming";

function renderReview({ job, events = [] }: { job: GenerationJob | null; events?: JobEvent[] }) {
  render(
    <ThemeProvider theme={kigTheme}>
      <GenerationReview
        events={events}
        job={job}
        selectedCandidateIndex={null}
        onRefresh={vi.fn()}
        onSelectCandidate={vi.fn()}
      />
    </ThemeProvider>,
  );
}

describe("GenerationReview", () => {
  beforeEach(async () => {
    window.localStorage.clear();
    await i18n.changeLanguage(DEFAULT_LOCALE);
    i18n.addResource(DEFAULT_LOCALE, "translation", "common.refresh", "刷新");
    i18n.addResource(DEFAULT_LOCALE, "translation", "generation.jobLabel", "任务 {{id}}");
    i18n.addResource(DEFAULT_LOCALE, "translation", "generation.progressAria", "生成进度");
    i18n.addResource(DEFAULT_LOCALE, "translation", "generation.waitingForProgress", "等待生成进度更新");
  });

  afterEach(async () => {
    cleanup();
    window.localStorage.clear();
    vi.useRealTimers();
    i18n.addResource(DEFAULT_LOCALE, "translation", "common.refresh", "刷新");
    i18n.addResource(DEFAULT_LOCALE, "translation", "generation.jobLabel", "任务 {{id}}");
    i18n.addResource(DEFAULT_LOCALE, "translation", "generation.progressAria", "生成进度");
    i18n.addResource(DEFAULT_LOCALE, "translation", "generation.waitingForProgress", "等待生成进度更新");
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it("renders localized waiting copy before a job exists", () => {
    renderReview({ job: null });

    expect(screen.getByRole("heading", { name: "等待生成" })).toBeInTheDocument();
    expect(screen.getByText("提交后这里会显示排队和生成进度。")).toBeInTheDocument();
  });

  it("shows only the current progress detail below the progress bar", () => {
    renderReview({
      job: makeJob([], { progress: 15, status: "preparing_references" }),
      events: [
        {
          sequence: 1,
          type: "queued",
          progress: 0,
          message: "排队中",
          created_at: "2026-06-23T12:00:00Z",
        },
        {
          sequence: 2,
          type: "preparing_references",
          progress: 15,
          message: "Preparing references",
          created_at: "2026-06-23T12:01:00Z",
        },
      ],
    });

    expect(screen.queryByText("任务进度")).toBeNull();
    expect(screen.getByTestId("generation-current-detail")).toHaveTextContent("准备素材");
  });

  it("renders fixed generation status labels from i18n resources without a linear progress bar", () => {
    i18n.addResource(DEFAULT_LOCALE, "translation", "common.refresh", "刷新测试");
    i18n.addResource(DEFAULT_LOCALE, "translation", "generation.jobLabel", "任务编号 {{id}}");
    i18n.addResource(DEFAULT_LOCALE, "translation", "generation.progressAria", "生成进度测试");
    i18n.addResource(DEFAULT_LOCALE, "translation", "generation.waitingForProgress", "等待进度测试");

    renderReview({
      job: makeJob([], {
        id: "job-abcdef12",
        phase_label: "",
        progress: 0,
        status: "",
      }),
    });

    expect(screen.getByTestId("generation-job-id")).toHaveTextContent("任务编号 job-abcd");
    expect(screen.getByRole("button", { name: "刷新测试" })).toBeInTheDocument();
    expect(screen.getByTestId("generation-spinner")).toBeInTheDocument();
    expect(screen.queryByLabelText("生成进度测试")).toBeNull();
    expect(screen.getByTestId("generation-current-detail")).toHaveTextContent("等待进度测试");
  });

  it("hides backend provider names in status and progress text", () => {
    renderReview({
      job: makeJob([], {
        phase_label: "Codex generating",
        progress: 45,
        status: "codex_generating",
      }),
      events: [
        {
          sequence: 1,
          type: "codex_generating",
          progress: 45,
          message: "Codex generating",
          created_at: "2026-06-23T12:00:00Z",
        },
      ],
    });

    expect(screen.getByTestId("generation-status")).toHaveTextContent("生成中");
    expect(screen.getByTestId("generation-current-detail")).toHaveTextContent("生成中");
    expect(screen.queryByText("45%")).toBeNull();
    expect(screen.queryByText(/codex/i)).toBeNull();
  });

  it("uses the benchmark ETA baseline when there is not enough history", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-23T12:01:30Z"));

    renderReview({
      job: makeJob([], {
        phase_label: "Codex generating",
        progress: 45,
        status: "codex_generating",
      }),
      events: [
        {
          sequence: 1,
          type: "codex_generating",
          progress: 45,
          message: "Codex generating",
          created_at: "2026-06-23T12:00:00Z",
        },
      ],
    });

    expect(screen.getByTestId("generation-elapsed")).toHaveTextContent("已用时间 1 分 30 秒");
    expect(screen.getByTestId("generation-eta")).toHaveTextContent("预计剩余 5 分钟");
    expect(screen.getByTestId("generation-eta")).not.toHaveTextContent("秒");
  });

  it("uses robust local history to show remaining time", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-23T12:02:00Z"));
    window.localStorage.setItem(
      GENERATION_DURATION_HISTORY_STORAGE_KEY,
      JSON.stringify([
        { durationMs: 300_000, finishedAt: "2026-06-22T12:00:00Z", jobId: "job-a" },
        { durationMs: 360_000, finishedAt: "2026-06-22T13:00:00Z", jobId: "job-b" },
        { durationMs: 420_000, finishedAt: "2026-06-22T14:00:00Z", jobId: "job-c" },
      ]),
    );

    renderReview({
      job: makeJob([], {
        phase_label: "Codex generating",
        progress: 45,
        status: "codex_generating",
      }),
      events: [
        {
          sequence: 1,
          type: "codex_generating",
          progress: 45,
          message: "Codex generating",
          created_at: "2026-06-23T12:00:00Z",
        },
      ],
    });

    expect(screen.getByTestId("generation-eta")).toHaveTextContent("预计剩余 4 分钟");
  });

  it("shows the live queue position while queued", () => {
    renderReview({
      job: makeJob([], {
        phase_label: "排队中",
        progress: 0,
        queue_position: 3,
        status: "queued",
      }),
    });

    expect(screen.getByTestId("generation-queue-position")).toHaveTextContent("排队序号 3");
  });
});

function makeJob(outputIndexes = [1], overrides: Partial<GenerationJob> = {}): GenerationJob {
  return {
    accepted_output_index: null,
    character_session_id: "session-1",
    expected_output_count: 1,
    generation_mode: "front_design",
    id: "job-123456",
    outputs: outputIndexes.map((index) => ({
      height: 1024,
      image_url: `/fixtures/generated/candidate-${index}.webp`,
      index,
      object_key: `candidate-${index}.webp`,
      width: 1024,
    })),
    phase_label: "生成完成",
    progress: 100,
    provider: "fixture",
    queue_position: null,
    status: "succeeded",
    token_usage: null,
    ...overrides,
  };
}
