import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getAuditGenerationJobs, getAuditSession, getAuditSummary, updateQuotaPolicy } from "../api/client";
import { kigTheme } from "../app/theme";
import { AdminAuditPage } from "./AdminAuditPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getAuditGenerationJobs: vi.fn(),
    getAuditSession: vi.fn(),
    getAuditSummary: vi.fn(),
    updateQuotaPolicy: vi.fn(),
  };
});

const getAuditGenerationJobsMock = vi.mocked(getAuditGenerationJobs);
const getAuditSessionMock = vi.mocked(getAuditSession);
const getAuditSummaryMock = vi.mocked(getAuditSummary);
const updateQuotaPolicyMock = vi.mocked(updateQuotaPolicy);

function renderAdminAuditPage() {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <ThemeProvider theme={kigTheme}>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AdminAuditPage />
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

describe("AdminAuditPage", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows audit metrics and saves edited quota policy", async () => {
    getAuditGenerationJobsMock.mockResolvedValue([
      {
        character_session_id: "session-records",
        created_at: "2026-07-02T00:00:00+00:00",
        generation_mode: "front_design",
        id: "job-record-1",
        outputs: [
          {
            height: 1100,
            image_url: "/api/generated/session/job/outputs/candidate-1.webp",
            index: 1,
            object_key: "codex/job/outputs/candidate-1.webp",
            width: 800,
          },
        ],
        phase_label: "生成完成",
        progress: 100,
        provider: "codex_bridge",
        references: [
          {
            description: "正面参考",
            image_url: "/api/audit/references/references/upload-a/front.webp",
            kind: "front",
            reference_key: "front:references/upload-a/front.webp",
          },
        ],
        requirement_ids: ["soft_eyes"],
        status: "succeeded",
        token_usage: {
          cached_input_tokens: 0,
          input_tokens: 12,
          jobs_with_usage: 1,
          output_tokens: 8,
          reasoning_output_tokens: 0,
          total_tokens: 20,
        },
        updated_at: "2026-07-02T00:01:00+00:00",
        user_notes: "眼神更柔和",
        user_requirements: "",
      },
    ]);
    getAuditSummaryMock.mockResolvedValue({
      active_users: 4,
      failure_rate: 0.0909,
      image_usage: {
        cached_input_tokens_per_image: 4.55,
        generated_images: 6,
        images_with_token_usage: 6,
        input_tokens_per_image: 18.18,
        jobs_with_outputs: 3,
        output_tokens_per_image: 5.45,
        reasoning_output_tokens_per_image: 1.82,
        total_tokens_per_image: 22.73,
      },
      job_counts: { queued: 2, succeeded: 8, failed: 1 },
      parallel_slots_used: 2,
      queue_length: 2,
      quota_policy: {
        window_hours: 5,
        normal_window_limit: 8,
        premium_unlimited: true,
        parallel_generation_limit: 8,
      },
      success_rate: 0.7273,
      token_usage: {
        cached_input_tokens: 25,
        input_tokens: 100,
        jobs_with_usage: 3,
        output_tokens: 30,
        reasoning_output_tokens: 10,
        total_tokens: 125,
      },
      total_calls: 11,
      total_users: 5,
    });
    getAuditSessionMock.mockResolvedValue({ authenticated: true });
    updateQuotaPolicyMock.mockResolvedValue({
      window_hours: 6,
      normal_window_limit: 10,
      premium_unlimited: false,
      parallel_generation_limit: 4,
    });

    renderAdminAuditPage();

    expect(await screen.findByText("总用户")).toBeInTheDocument();
    expect(screen.getByText("总调用")).toBeInTheDocument();
    expect(screen.getByTestId("audit-overview-charts")).toBeInTheDocument();
    expect(screen.getByTestId("audit-user-chart")).toHaveTextContent("累计用户");
    expect(screen.getByTestId("audit-call-chart")).toHaveTextContent("每日调用");
    expect(screen.getByText("生成图片")).toBeInTheDocument();
    expect(screen.getByTestId("audit-generated-images")).toHaveTextContent("6");
    expect(screen.getByTestId("audit-total-tokens-per-image")).toHaveTextContent("22.73");
    expect(screen.getByText("11")).toBeInTheDocument();
    expect(await screen.findByText("生图记录")).toBeInTheDocument();
    expect(screen.getByTestId("audit-generation-job-job-record-1")).toHaveTextContent("生成完成");
    expect(screen.getByTestId("audit-generation-job-detail")).toHaveTextContent("眼神更柔和");
    expect(screen.getByTestId("audit-reference-image-0")).toHaveAttribute(
      "src",
      "/api/audit/references/references/upload-a/front.webp",
    );
    expect(screen.getByTestId("audit-output-image-0")).toHaveAttribute(
      "src",
      "/api/generated/session/job/outputs/candidate-1.webp",
    );

    fireEvent.change(screen.getByLabelText("统计窗口（小时）"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("普通用户窗口配额"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("并发生成上限"), { target: { value: "4" } });
    fireEvent.click(screen.getByLabelText("高级用户不受普通配额限制"));
    fireEvent.click(screen.getByRole("button", { name: "保存策略" }));

    await waitFor(() =>
      expect(updateQuotaPolicyMock).toHaveBeenCalledWith({
        window_hours: 6,
        normal_window_limit: 10,
        premium_unlimited: false,
        parallel_generation_limit: 4,
      }),
    );
  });
});
