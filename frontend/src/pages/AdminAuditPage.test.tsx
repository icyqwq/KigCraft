import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  activateCodexProfile,
  adjustAdminUserDailyGenerationQuota,
  cancelCodexProfileDeviceAuth,
  deleteCodexProfile,
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
  startCodexDeviceAuth,
  setActiveProviderPreset,
  revokeAdminUserSessions,
  updateAdminUserBan,
  updateAdminUserDailyGenerationLimit,
  updateAdminUserRole,
  updateQuotaPolicy,
  type AdminUser,
} from "../api/client";
import { kigTheme } from "../app/theme";
import { useAuth } from "../features/auth/AuthProvider";
import { AdminAuditPage, formatTrendXAxisLabel } from "./AdminAuditPage";

vi.mock("../features/auth/AuthProvider", () => ({
  useAuth: vi.fn(),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    activateCodexProfile: vi.fn(),
    adjustAdminUserDailyGenerationQuota: vi.fn(),
    cancelCodexProfileDeviceAuth: vi.fn(),
    deleteCodexProfile: vi.fn(),
    getCodexProfileDeviceAuth: vi.fn(),
    getCodexUpdateStatus: vi.fn(),
    getAuditGenerationJobsPage: vi.fn(),
    getAuditSummary: vi.fn(),
    getCodexUsageHistory: vi.fn().mockResolvedValue({ plan_type: "prolite", points: [
      { at: "2026-10-04T01:00:00+00:00", primary_remaining_percent: 90, primary_window_minutes: 10080, secondary_remaining_percent: 60, secondary_window_minutes: 300 },
      { at: "2026-10-04T02:00:00+00:00", primary_remaining_percent: 85.5, primary_window_minutes: 10080, secondary_remaining_percent: 40, secondary_window_minutes: 300 },
    ] }),
    getProviderPresets: vi.fn(),
    getProviderUsage: vi.fn(),
    listAdminUsersPage: vi.fn(),
    listCodexProfiles: vi.fn(),
    resetAdminUserDailyGenerationQuota: vi.fn(),
    resetDailyGenerationQuota: vi.fn(),
    startCodexDeviceAuth: vi.fn(),
    setActiveProviderPreset: vi.fn(),
    revokeAdminUserSessions: vi.fn(),
    updateAdminUserBan: vi.fn(),
    updateAdminUserDailyGenerationLimit: vi.fn(),
    updateAdminUserRole: vi.fn(),
    updateQuotaPolicy: vi.fn(),
  };
});

vi.mock("../components/charts/EChart", () => ({
  EChart: ({ ariaLabel, className }: { ariaLabel: string; className?: string }) => (
    <div aria-label={ariaLabel} className={className} data-testid="echarts-chart" />
  ),
}));

const activateCodexProfileMock = vi.mocked(activateCodexProfile);
const adjustAdminUserDailyGenerationQuotaMock = vi.mocked(adjustAdminUserDailyGenerationQuota);
const cancelCodexProfileDeviceAuthMock = vi.mocked(cancelCodexProfileDeviceAuth);
const deleteCodexProfileMock = vi.mocked(deleteCodexProfile);
const getCodexProfileDeviceAuthMock = vi.mocked(getCodexProfileDeviceAuth);
const getCodexUpdateStatusMock = vi.mocked(getCodexUpdateStatus);
const getAuditGenerationJobsPageMock = vi.mocked(getAuditGenerationJobsPage);
const getAuditSummaryMock = vi.mocked(getAuditSummary);
const getProviderPresetsMock = vi.mocked(getProviderPresets);
const getProviderUsageMock = vi.mocked(getProviderUsage);
const listAdminUsersPageMock = vi.mocked(listAdminUsersPage);
const listCodexProfilesMock = vi.mocked(listCodexProfiles);
const resetAdminUserDailyGenerationQuotaMock = vi.mocked(resetAdminUserDailyGenerationQuota);
const resetDailyGenerationQuotaMock = vi.mocked(resetDailyGenerationQuota);
const startCodexDeviceAuthMock = vi.mocked(startCodexDeviceAuth);
const setActiveProviderPresetMock = vi.mocked(setActiveProviderPreset);
const revokeAdminUserSessionsMock = vi.mocked(revokeAdminUserSessions);
const updateAdminUserBanMock = vi.mocked(updateAdminUserBan);
const updateAdminUserDailyGenerationLimitMock = vi.mocked(updateAdminUserDailyGenerationLimit);
const updateAdminUserRoleMock = vi.mocked(updateAdminUserRole);
const updateQuotaPolicyMock = vi.mocked(updateQuotaPolicy);
const useAuthMock = vi.mocked(useAuth);

const adminUser = {
  currentUser: {
    email: "admin@example.com",
    id: "admin-1",
    is_banned: false,
    role: "admin" as const,
  },
  isAdmin: true,
  isLoading: false,
  logout: vi.fn(),
  refreshAuth: vi.fn(),
};

const managedUser: AdminUser = {
  call_count: 2,
  created_at: "2026-07-08T00:00:00Z",
  daily_generation_limit: 10,
  daily_generation_limit_override: null,
  daily_generation_balance: 18,
  daily_generation_remaining: 18,
  daily_generation_reset_at: "2026-07-10T06:00:00+08:00",
  daily_generation_used: 2,
  email: "managed@example.com",
  generation_count: 2,
  id: "user-1",
  is_banned: false,
  last_login_at: null,
  role: "user",
  task_count: 2,
};

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
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function mockAuditData() {
  getAuditGenerationJobsPageMock.mockResolvedValue({
    has_more: false,
    items: [
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
        references: [],
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
    ],
    next_cursor: null,
  });
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
    job_counts: { failed: 1, queued: 2, succeeded: 8 },
    parallel_slots_used: 2,
    queue_length: 2,
    quota_policy: {
      codex_usage_min_remaining_percent: 25,
      daily_generation_custom_limit_default: 20,
      daily_generation_limit: 10,
      daily_generation_reset_hour: 6,
      parallel_generation_limit: 4,
      window_hours: 5,
      window_limit: 8,
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
  getProviderPresetsMock.mockResolvedValue({
    active: { preset_id: "codex_full", updated_at: "", updated_by: "" },
    presets: [
      {
        analysis_model: "gpt-5.5",
        analysis_provider: "codex",
        id: "codex_full",
        image_model: "gpt-5.5",
        image_provider: "codex",
        label: "Codex",
      },
    ],
  });
  getProviderUsageMock.mockResolvedValue([]);
  setActiveProviderPresetMock.mockResolvedValue({ preset_id: "codex_full", updated_at: "", updated_by: "admin-1" });
  listAdminUsersPageMock.mockImplementation(async (input = {}) => ({
    has_more: (input.offset ?? 0) === 0,
    limit: input.limit ?? 25,
    offset: input.offset ?? 0,
    sort_by: input.sortBy ?? "created_at",
    sort_dir: input.sortDir ?? "desc",
    total: 2,
    users: (input.offset ?? 0) === 0
      ? [managedUser]
      : [{ ...managedUser, email: "next@example.com", id: "user-2" }],
  }));
  listCodexProfilesMock.mockResolvedValue([{
    id: "__default__",
    name: "默认账号",
    is_active: true,
    is_builtin: true,
    credential_files: ["auth.json", "config.toml"],
    created_at: "2026-07-09T00:00:00Z",
    updated_at: "2026-07-09T00:00:00Z",
  }, {
    id: "account-a",
    name: "Account A",
    is_active: false,
    is_builtin: false,
    credential_files: ["auth.json"],
    created_at: "2026-07-09T00:00:00Z",
    updated_at: "2026-07-09T00:00:00Z",
  }, {
    id: "account-empty",
    name: "Account Empty",
    is_active: false,
    is_builtin: false,
    credential_files: [],
    created_at: "2026-07-09T00:00:00Z",
    updated_at: "2026-07-09T00:00:00Z",
  }]);
  getCodexUpdateStatusMock.mockResolvedValue({
    available: true,
    checked_at: "2026-07-12T01:02:03+00:00",
    version_before: "codex-cli 1.2.2",
    version_after: "codex-cli 1.2.3",
    succeeded: false,
    error: "update_failed_exit_7",
  });
  activateCodexProfileMock.mockResolvedValue({
    id: "account-a",
    name: "Account A",
    is_active: true,
    credential_files: ["auth.json"],
    created_at: "2026-07-09T00:00:00Z",
    updated_at: "2026-07-09T00:01:00Z",
  });
  deleteCodexProfileMock.mockResolvedValue({ ok: true });
  startCodexDeviceAuthMock.mockResolvedValue({
    id: "device-session-1",
    profile_id: "pending",
    status: "running",
    output: "Visit https://example.com/device\nCode: ABCD-EFGH",
    error: null,
    created_at: "2026-07-09T00:02:00Z",
    completed_at: null,
  });
  getCodexProfileDeviceAuthMock.mockResolvedValue({
    id: "device-session-1",
    profile_id: "account-a",
    status: "running",
    output: "Open browser",
    error: null,
    created_at: "2026-07-09T00:02:00Z",
    completed_at: null,
  });
  cancelCodexProfileDeviceAuthMock.mockResolvedValue({
    id: "device-session-1",
    profile_id: "account-a",
    status: "cancelled",
    output: "Open browser",
    error: null,
    created_at: "2026-07-09T00:02:00Z",
    completed_at: "2026-07-09T00:03:00Z",
  });
  updateAdminUserBanMock.mockResolvedValue({ ...managedUser, is_banned: true });
  updateAdminUserRoleMock.mockResolvedValue({ ...managedUser, role: "admin" });
  revokeAdminUserSessionsMock.mockResolvedValue({ revoked_sessions: 1 });
  resetDailyGenerationQuotaMock.mockResolvedValue({ reset_count: 2, window_start_at: "2026-07-09T06:00:00+08:00" });
  resetAdminUserDailyGenerationQuotaMock.mockResolvedValue({ reset_count: 1, window_start_at: "2026-07-09T06:00:00+08:00" });
  adjustAdminUserDailyGenerationQuotaMock.mockResolvedValue({ ...managedUser, daily_generation_balance: 23, daily_generation_remaining: 23 });
  updateAdminUserDailyGenerationLimitMock.mockResolvedValue({ ...managedUser, daily_generation_limit: 100, daily_generation_limit_override: 100 });
  updateQuotaPolicyMock.mockResolvedValue({
    codex_usage_min_remaining_percent: 25,
    daily_generation_custom_limit_default: 20,
    daily_generation_limit: 10,
    daily_generation_reset_hour: 6,
    parallel_generation_limit: 4,
    window_hours: 6,
    window_limit: 10,
  });
}

describe("AdminAuditPage", () => {
  afterEach(() => {
    cleanup();
    window.location.hash = "";
    vi.clearAllMocks();
  });

  it("keeps daily trend labels visible on the main chart axis", () => {
    expect(formatTrendXAxisLabel("07-10")).toBe("07-10");
    expect(formatTrendXAxisLabel("07-10 14:00")).toBe("14:00");
  });

  it("renders the dashboard trends and KPI sparklines with ECharts", async () => {
    useAuthMock.mockReturnValue(adminUser);
    mockAuditData();
    renderAdminAuditPage();

    expect(await screen.findAllByTestId("echarts-chart")).toHaveLength(6);
    expect(screen.getByLabelText("用量趋势图")).toBeInTheDocument();
  });

  it("shows the Codex remaining-quota curve inside the usage trend and switches its range", async () => {
    useAuthMock.mockReturnValue(adminUser);
    mockAuditData();
    renderAdminAuditPage();

    const panel = await screen.findByTestId("audit-codex-remaining");
    expect(within(panel).getByLabelText("Codex 剩余用量曲线")).toBeInTheDocument();
    expect(await within(panel).findByText("当前 85.5%")).toBeInTheDocument();
    expect(vi.mocked(getCodexUsageHistory)).toHaveBeenCalledWith(168);
    fireEvent.click(within(panel).getByRole("button", { name: "30 天" }));
    await waitFor(() => expect(vi.mocked(getCodexUsageHistory)).toHaveBeenCalledWith(720));
  });

  it("offers only Codex while retaining historical Seedream usage", async () => {
    useAuthMock.mockReturnValue(adminUser);
    mockAuditData();
    getProviderUsageMock.mockResolvedValue([
      {
        calls: 3,
        failures: 1,
        generated_images: 2,
        input_tokens: null,
        output_tokens: 20,
        provider: "seedream",
        successes: 2,
        total_tokens: 20,
      },
    ]);

    renderAdminAuditPage();

    fireEvent.change(await screen.findByLabelText("生成提供方预设"), { target: { value: "codex_full" } });
    fireEvent.click(screen.getByRole("button", { name: "保存提供方预设" }));

    await waitFor(() => expect(setActiveProviderPresetMock).toHaveBeenCalledWith("codex_full"));
    expect(await screen.findByText("Seedream")).toBeInTheDocument();
    expect(screen.queryByRole("option", {name:"Qwen + Seedream"})).not.toBeInTheDocument();
  });

  it("renders admin-only user management and performs search and user actions", async () => {
    useAuthMock.mockReturnValue(adminUser);
    mockAuditData();

    const { container } = renderAdminAuditPage();

    expect(await screen.findByText("KigCraft Admin")).toBeInTheDocument();
    expect(screen.queryByText("用户管理")).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "账号" })[0]!);
    expect(await screen.findByText("用户管理")).toBeInTheDocument();
    expect(await screen.findByText("managed@example.com")).toBeInTheDocument();
    expect(screen.getByText("2/10")).toBeInTheDocument();
    expect(screen.getByText("全局额度")).toBeInTheDocument();
    await waitFor(() =>
      expect(listAdminUsersPageMock).toHaveBeenCalledWith({
        limit: 25,
        offset: 0,
        query: "",
        sortBy: "created_at",
        sortDir: "desc",
      }),
    );

    fireEvent.change(screen.getByLabelText("搜索用户"), { target: { value: "managed" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() =>
      expect(listAdminUsersPageMock).toHaveBeenLastCalledWith({
        limit: 25,
        offset: 0,
        query: "managed",
        sortBy: "created_at",
        sortDir: "desc",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "生成数" }));
    await waitFor(() =>
      expect(listAdminUsersPageMock).toHaveBeenLastCalledWith({
        limit: 25,
        offset: 0,
        query: "managed",
        sortBy: "generation_count",
        sortDir: "desc",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    await waitFor(() =>
      expect(listAdminUsersPageMock).toHaveBeenLastCalledWith({
        limit: 25,
        offset: 25,
        query: "managed",
        sortBy: "generation_count",
        sortDir: "desc",
      }),
    );

    const firstUserActionButton = () => screen.getAllByTestId("admin-user-actions-user-1")[0]!;

    fireEvent.click(firstUserActionButton());
    fireEvent.click(screen.getByRole("menuitem", { name: "封禁" }));
    fireEvent.click(screen.getByRole("button", { name: "确认封禁" }));
    await waitFor(() => expect(updateAdminUserBanMock).toHaveBeenCalledWith("user-1", true));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(firstUserActionButton());
    fireEvent.click(screen.getByRole("menuitem", { name: "设为管理员" }));
    fireEvent.click(screen.getByRole("button", { name: "确认设为管理员" }));
    await waitFor(() => expect(updateAdminUserRoleMock).toHaveBeenCalledWith("user-1", "admin"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(firstUserActionButton());
    fireEvent.click(screen.getByRole("menuitem", { name: "强制下线" }));
    fireEvent.click(screen.getByRole("button", { name: "确认强制下线" }));
    await waitFor(() => expect(revokeAdminUserSessionsMock).toHaveBeenCalledWith("user-1"));

    fireEvent.click(firstUserActionButton());
    fireEvent.click(screen.getByTestId("admin-user-enable-custom-daily-quota"));
    await waitFor(() => expect(updateAdminUserDailyGenerationLimitMock).toHaveBeenCalledWith("user-1", 20));

    fireEvent.click(firstUserActionButton());
    fireEvent.click(screen.getByTestId("admin-user-reset-daily-quota"));
    await waitFor(() => expect(resetAdminUserDailyGenerationQuotaMock).toHaveBeenCalledWith("user-1"));

    const promptMock = vi.spyOn(window, "prompt").mockReturnValueOnce("5").mockReturnValueOnce("3");
    fireEvent.click(firstUserActionButton());
    fireEvent.click(screen.getByTestId("admin-user-add-daily-quota"));
    await waitFor(() => expect(adjustAdminUserDailyGenerationQuotaMock).toHaveBeenCalledWith("user-1", 5));

    fireEvent.click(firstUserActionButton());
    fireEvent.click(screen.getByTestId("admin-user-subtract-daily-quota"));
    await waitFor(() => expect(adjustAdminUserDailyGenerationQuotaMock).toHaveBeenCalledWith("user-1", -3));
    promptMock.mockRestore();

    expect(container.textContent).not.toMatch(/premium|normal|advanced|高级|普通/i);
  });

  it("saves daily quota policy and can reset all daily quota", async () => {
    useAuthMock.mockReturnValue(adminUser);
    mockAuditData();
    window.location.hash = "#audit-quota";

    renderAdminAuditPage();

    expect(await screen.findByText("KigCraft Admin")).toBeInTheDocument();

    expect(screen.getByTestId("quota-daily-generation-limit")).toBeInTheDocument();
    expect(screen.getByTestId("quota-daily-generation-custom-limit-default")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("quota-daily-generation-reset-all"));
    await waitFor(() => expect(resetDailyGenerationQuotaMock).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByTestId("quota-policy-save"));
    await waitFor(() =>
      expect(updateQuotaPolicyMock).toHaveBeenCalledWith(expect.objectContaining({
        daily_generation_custom_limit_default: 20,
        daily_generation_limit: 10,
        daily_generation_reset_hour: 6,
      })),
    );

    expect(await screen.findByTestId("codex-profiles-panel")).toBeInTheDocument();
    await waitFor(() => expect(listCodexProfilesMock).toHaveBeenCalledTimes(1));
    expect(screen.getAllByText(/^添加时间：/)).toHaveLength(3);

    const switchButtons = screen.getAllByRole("button", { name: "切换" });
    expect(switchButtons[0]).toBeDisabled();
    expect(switchButtons[1]).not.toBeDisabled();
    expect(switchButtons[2]).toBeDisabled();

    fireEvent.click(switchButtons[1]);
    await waitFor(() => expect(activateCodexProfileMock).toHaveBeenCalledWith("account-a"));

    fireEvent.click(screen.getByTestId("codex-profile-delete-account-empty"));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(deleteCodexProfileMock).toHaveBeenCalledWith("account-empty"));
  });

  it("offers one-click codex login, polls, and refreshes profiles after success", async () => {
    useAuthMock.mockReturnValue(adminUser);
    mockAuditData();
    window.location.hash = "#audit-quota";
    let poll: TimerHandler | undefined;
    const intervalSpy = vi.spyOn(window, "setInterval").mockImplementation((handler: TimerHandler, timeout?: number) => {
      if (timeout === 1000) poll = handler;
      return 1;
    });
    getCodexProfileDeviceAuthMock.mockResolvedValueOnce({
      id: "device-session-1",
      profile_id: "pending",
      status: "succeeded",
      output: "Login complete",
      error: null,
      created_at: "2026-07-09T00:02:00Z",
      completed_at: "2026-07-09T00:03:00Z",
    });

    renderAdminAuditPage();

    expect(await screen.findByTestId("codex-profiles-panel")).toBeInTheDocument();
    expect(screen.queryByLabelText("账号 ID")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("显示名称")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("粘贴 Codex CLI 凭证内容")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "添加 Codex 登录" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "添加 Codex 登录" }));
    await waitFor(() => expect(startCodexDeviceAuthMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/ABCD-EFGH/)).toHaveStyle({ whiteSpace: "pre-wrap" });
    await waitFor(() => expect(poll).toEqual(expect.any(Function)));

    await act(async () => {
      (poll as () => void)();
    });

    await waitFor(() => expect(getCodexProfileDeviceAuthMock).toHaveBeenCalledWith("pending", "device-session-1"));
    await waitFor(() => expect(listCodexProfilesMock).toHaveBeenCalledTimes(2));
    intervalSpy.mockRestore();
  });

  it("shows the codex cli version and a safe update warning", async () => {
    useAuthMock.mockReturnValue(adminUser);
    mockAuditData();
    window.location.hash = "#audit-quota";

    renderAdminAuditPage();

    expect(await screen.findByText("codex-cli 1.2.3")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("update_failed_exit_7");
  });

  it("does not let an overlapping poll revive a cancelled login", async () => {
    useAuthMock.mockReturnValue(adminUser);
    mockAuditData();
    window.location.hash = "#audit-quota";
    let poll: TimerHandler | undefined;
    const intervalSpy = vi.spyOn(window, "setInterval").mockImplementation((handler: TimerHandler, timeout?: number) => {
      if (timeout === 1000) poll = handler;
      return 1;
    });
    const staleRefresh = deferred<Awaited<ReturnType<typeof getCodexProfileDeviceAuth>>>();
    getCodexProfileDeviceAuthMock.mockReturnValueOnce(staleRefresh.promise);

    renderAdminAuditPage();
    fireEvent.click(await screen.findByRole("button", { name: "添加 Codex 登录" }));
    await screen.findByText(/ABCD-EFGH/);
    await waitFor(() => expect(poll).toEqual(expect.any(Function)));

    act(() => (poll as () => void)());
    await waitFor(() => expect(getCodexProfileDeviceAuthMock).toHaveBeenCalledTimes(1));
    act(() => (poll as () => void)());
    expect(getCodexProfileDeviceAuthMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "取消登录" }));
    expect(await screen.findByText("设备登录状态：cancelled")).toBeInTheDocument();

    await act(async () => {
      staleRefresh.resolve({
        id: "device-session-1",
        profile_id: "pending",
        status: "running",
        output: "stale running result",
        error: null,
        created_at: "2026-07-09T00:02:00Z",
        completed_at: null,
      });
      await staleRefresh.promise;
    });

    expect(screen.getByText("设备登录状态：cancelled")).toBeInTheDocument();
    expect(screen.queryByText("stale running result")).not.toBeInTheDocument();
    intervalSpy.mockRestore();
  });

  it("prevents concurrent login starts while pending and running", async () => {
    useAuthMock.mockReturnValue(adminUser);
    mockAuditData();
    window.location.hash = "#audit-quota";
    const pendingStart = deferred<Awaited<ReturnType<typeof startCodexDeviceAuth>>>();
    startCodexDeviceAuthMock.mockReturnValueOnce(pendingStart.promise);

    renderAdminAuditPage();
    const button = await screen.findByRole("button", { name: "添加 Codex 登录" });
    fireEvent.click(button);

    await waitFor(() => expect(startCodexDeviceAuthMock).toHaveBeenCalledTimes(1));
    expect(button).toBeDisabled();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    fireEvent.click(button);
    expect(startCodexDeviceAuthMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingStart.resolve({
        id: "device-session-1",
        profile_id: "pending",
        status: "running",
        output: "ABCD-EFGH",
        error: null,
        created_at: "2026-07-09T00:02:00Z",
        completed_at: null,
      });
      await pendingStart.promise;
    });

    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(startCodexDeviceAuthMock).toHaveBeenCalledTimes(1);
  });

  it("shows a compact no-permission panel for non-admin accounts", () => {
    useAuthMock.mockReturnValue({
      ...adminUser,
      currentUser: { ...adminUser.currentUser, role: "user" },
      isAdmin: false,
    });

    renderAdminAuditPage();

    expect(screen.getByText("无权限访问")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回工作台" })).toHaveAttribute("href", "/v2");
    expect(getAuditSummaryMock).not.toHaveBeenCalled();
    expect(listAdminUsersPageMock).not.toHaveBeenCalled();
  });

  it("shows an admin data error panel when the summary request fails", async () => {
    useAuthMock.mockReturnValue(adminUser);
    getAuditSummaryMock.mockRejectedValueOnce(new Error("summary failed"));

    renderAdminAuditPage();

    expect(await screen.findByText("管理数据暂时不可用")).toBeInTheDocument();
    expect(screen.getByText("审计汇总加载失败，请稍后重试。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(getAuditSummaryMock).toHaveBeenCalledTimes(2));
  });

  it("shows an explicit user list error state when the admin user query fails", async () => {
    useAuthMock.mockReturnValue(adminUser);
    getAuditGenerationJobsPageMock.mockResolvedValue({ has_more: false, items: [], next_cursor: null });
    getAuditSummaryMock.mockResolvedValue({
      active_users: 4,
      failure_rate: 0,
      image_usage: {
        cached_input_tokens_per_image: 0,
        generated_images: 0,
        images_with_token_usage: 0,
        input_tokens_per_image: 0,
        jobs_with_outputs: 0,
        output_tokens_per_image: 0,
        reasoning_output_tokens_per_image: 0,
        total_tokens_per_image: 0,
      },
      job_counts: { failed: 0, queued: 0, succeeded: 1 },
      parallel_slots_used: 0,
      queue_length: 0,
      quota_policy: {
        parallel_generation_limit: 4,
        window_hours: 5,
        window_limit: 8,
      },
      success_rate: 1,
      token_usage: {
        cached_input_tokens: 0,
        input_tokens: 0,
        jobs_with_usage: 0,
        output_tokens: 0,
        reasoning_output_tokens: 0,
        total_tokens: 0,
      },
      total_calls: 1,
      total_users: 1,
    });
    listAdminUsersPageMock.mockRejectedValueOnce(new Error("users failed"));

    renderAdminAuditPage();

    expect(await screen.findByText("KigCraft Admin")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "账号" })[0]!);
    expect(await screen.findByText("用户列表加载失败，请重试。")).toBeInTheDocument();
    expect(screen.queryByText("暂无用户记录。")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重试用户列表" }));
    await waitFor(() => expect(listAdminUsersPageMock).toHaveBeenCalledTimes(2));
  });
});
