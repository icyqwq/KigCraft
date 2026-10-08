import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { registerAccount, getAuthCaptchaConfig, sendAuthCode } from "../../api/client";
import { writeAuthDialogOpen, writeAuthFormDraft } from "./authFormDraft";
import { kigTheme } from "../../app/theme";
import i18n from "../../i18n";
import { DEFAULT_LOCALE } from "../../i18n/locales";
import { AuthProvider } from "./AuthProvider";
import { useRequireAuth } from "./useRequireAuth";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getCurrentUser: vi.fn().mockResolvedValue(null),
    getAuthCaptchaConfig: vi.fn(),
    registerAccount: vi.fn(),
    sendAuthCode: vi.fn(),
  };
});

const registerAccountMock = vi.mocked(registerAccount);
const getAuthCaptchaConfigMock = vi.mocked(getAuthCaptchaConfig);
const sendAuthCodeMock = vi.mocked(sendAuthCode);

function AuthDialogHarness({ rerenderToken = 0 }: { rerenderToken?: number }) {
  const { openAuthDialog, authDialog } = useRequireAuth();

  return (
    <div data-rerender-token={rerenderToken}>
      <button type="button" onClick={() => openAuthDialog()}>
        Open auth dialog
      </button>
      {authDialog}
    </div>
  );
}

function renderHarness(rerenderToken = 0) {
  const client = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });

  return render(
    <ThemeProvider theme={kigTheme}>
      <QueryClientProvider client={client}>
        <AuthProvider>
          <AuthDialogHarness rerenderToken={rerenderToken} />
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

function fillInput(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(new RegExp(`^${label}`)), { target: { value } });
}

describe("useRequireAuth", () => {
  beforeEach(async () => {
    await i18n.changeLanguage(DEFAULT_LOCALE);
    sessionStorage.clear();
    getAuthCaptchaConfigMock.mockResolvedValue({ enabled: false, site_key: null });
    registerAccountMock.mockResolvedValue({
      id: "user-1",
      email: "person@example.com",
      role: "user",
      is_banned: false,
    });
    sendAuthCodeMock.mockResolvedValue({ ok: true });
  });

  afterEach(async () => {
    cleanup();
    sessionStorage.clear();
    await i18n.changeLanguage(DEFAULT_LOCALE);
    vi.clearAllMocks();
  });

  it("keeps the auth dialog open while switching to registration and sending a code", async () => {
    renderHarness();

    fireEvent.click(screen.getByRole("button", { name: "Open auth dialog" }));
    expect(screen.getByRole("tab", { name: "注册" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    expect(screen.getByRole("button", { name: "发送验证码" })).toBeInTheDocument();

    fillInput("邮箱", "person@example.com");
    await waitFor(() => expect(screen.getByRole("button", { name: "发送验证码" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));

    await waitFor(() =>
      expect(sendAuthCodeMock).toHaveBeenCalledWith({
        email: "person@example.com",
        purpose: "register",
        captchaToken: null,
      }),
    );

    expect(screen.getByRole("tab", { name: "注册" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: /发送验证码|秒后可重发/ })).toBeInTheDocument();
  });

  it("preserves registration mode across parent rerenders while the dialog stays open", () => {
    function StatefulHarness() {
      const [count, setCount] = useState(0);
      return (
        <>
          <button type="button" onClick={() => setCount((value) => value + 1)}>
            Bump parent {count}
          </button>
          <AuthDialogHarness rerenderToken={count} />
        </>
      );
    }

    const client = new QueryClient({
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    });

    render(
      <ThemeProvider theme={kigTheme}>
        <QueryClientProvider client={client}>
          <AuthProvider>
            <StatefulHarness />
          </AuthProvider>
        </QueryClientProvider>
      </ThemeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open auth dialog" }));
    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    expect(screen.getByRole("tab", { name: "注册" })).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByRole("button", { name: /Bump parent/ }));
    expect(screen.getByRole("tab", { name: "注册" })).toHaveAttribute("aria-selected", "true");
  });

  it("preserves registration draft after closing and reopening the dialog", () => {
    renderHarness();

    fireEvent.click(screen.getByRole("button", { name: "Open auth dialog" }));
    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fillInput("邮箱", "person@example.com");
    fillInput("验证码", "123456");
    fillInput("密码", "new-password");

    const harness = screen.getByRole("button", { name: "Open auth dialog" }).parentElement;
    const backdrop = harness?.lastElementChild as HTMLElement | null;
    expect(backdrop).not.toBeNull();
    fireEvent.mouseDown(backdrop as HTMLElement);
    expect(screen.queryByRole("tab", { name: "注册" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open auth dialog" }));

    expect(screen.getByRole("tab", { name: "注册" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText(/^邮箱/)).toHaveValue("person@example.com");
    expect(screen.getByLabelText(/^验证码/)).toHaveValue("123456");
    expect(screen.getByLabelText(/^密码/)).toHaveValue("new-password");
  });

  it("reopens the auth dialog after a browser reload when it was left open", () => {
    writeAuthFormDraft({
      mode: "register",
      loginMethod: "password",
      email: "person@example.com",
      password: "new-password",
      code: "123456",
      termsAccepted: false,
      sendCooldownEndsAt: null,
    });
    writeAuthDialogOpen(true);

    renderHarness();

    expect(screen.getByRole("tab", { name: "注册" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText(/^邮箱/)).toHaveValue("person@example.com");
  });
});
