import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  getAuthCaptchaConfig,
  getCurrentTermsVersion,
  loginWithCode,
  loginWithPassword,
  registerAccount,
  resetPassword,
  sendAuthCode,
} from "../../api/client";
import { kigTheme } from "../../app/theme";
import i18n from "../../i18n";
import { DEFAULT_LOCALE } from "../../i18n/locales";
import { clearAuthFormDraft, writeAuthFormDraft } from "./authFormDraft";
import { AuthPage } from "./AuthPage";
import { useAuth } from "./AuthProvider";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getAuthCaptchaConfig: vi.fn(),
    getCurrentTermsVersion: vi.fn(),
    loginWithCode: vi.fn(),
    loginWithPassword: vi.fn(),
    registerAccount: vi.fn(),
    resetPassword: vi.fn(),
    sendAuthCode: vi.fn(),
  };
});

vi.mock("./AuthProvider", () => ({
  useAuth: vi.fn(),
}));

vi.mock("./TurnstileCaptcha", () => ({
  TurnstileCaptcha: ({
    onTokenChange,
    resetSignal,
  }: {
    onTokenChange: (token: string) => void;
    resetSignal: number;
  }) => (
    <button type="button" onClick={() => onTokenChange("captcha-token")}>
      captcha-widget-{resetSignal}
    </button>
  ),
}));

const getAuthCaptchaConfigMock = vi.mocked(getAuthCaptchaConfig);
const getCurrentTermsVersionMock = vi.mocked(getCurrentTermsVersion);
const loginWithCodeMock = vi.mocked(loginWithCode);
const loginWithPasswordMock = vi.mocked(loginWithPassword);
const registerAccountMock = vi.mocked(registerAccount);
const resetPasswordMock = vi.mocked(resetPassword);
const sendAuthCodeMock = vi.mocked(sendAuthCode);
const useAuthMock = vi.mocked(useAuth);

const currentUser = {
  id: "user-1",
  email: "person@example.com",
  role: "user" as const,
  is_banned: false,
};

function renderAuthPage() {
  const client = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
  return render(
    <ThemeProvider theme={kigTheme}>
      <QueryClientProvider client={client}>
        <AuthPage />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

function fillInput(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(new RegExp(`^${label}`)), { target: { value } });
}

describe("AuthPage", () => {
  const refreshAuth = vi.fn();

  beforeEach(async () => {
    await i18n.changeLanguage(DEFAULT_LOCALE);
    sessionStorage.clear();
    refreshAuth.mockResolvedValue({user:currentUser,epoch:1});
    useAuthMock.mockReturnValue({
      currentUser: null,
      isAdmin: false,
      isLoading: false,
      logout: vi.fn(),
      refreshAuth,
      authSessionEpoch: 0,
      isCurrentAuthSession: () => true,
    });
    getAuthCaptchaConfigMock.mockResolvedValue({ enabled: false, site_key: null });
    getCurrentTermsVersionMock.mockResolvedValue({ version: "server-terms-v2" });
    loginWithCodeMock.mockResolvedValue(currentUser);
    loginWithPasswordMock.mockResolvedValue(currentUser);
    registerAccountMock.mockResolvedValue(currentUser);
    resetPasswordMock.mockResolvedValue({ ok: true });
    sendAuthCodeMock.mockResolvedValue({ ok: true });
  });

  afterEach(async () => {
    cleanup();
    sessionStorage.clear();
    await i18n.changeLanguage(DEFAULT_LOCALE);
    vi.clearAllMocks();
  });

  it("switches between login, registration, and password reset modes", () => {
    renderAuthPage();

    expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    expect(screen.getByLabelText(/^验证码/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送验证码" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "注册" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "重置密码" }));
    expect(screen.getByLabelText(/^验证码/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重置密码" })).toBeInTheDocument();
  });

  it("restores registration draft after remounting the page", () => {
    const { unmount } = renderAuthPage();

    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fillInput("邮箱", "person@example.com");
    fillInput("验证码", "123456");
    fillInput("密码", "new-password");
    fireEvent.click(screen.getByRole("checkbox"));

    unmount();
    renderAuthPage();

    expect(screen.getByRole("tab", { name: "注册" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText(/^邮箱/)).toHaveValue("person@example.com");
    expect(screen.getByLabelText(/^验证码/)).toHaveValue("123456");
    expect(screen.getByLabelText(/^密码/)).toHaveValue("new-password");
    expect(screen.getByRole("checkbox")).toBeChecked();
  });

  it("restores registration draft when the browser tab becomes visible again", () => {
    renderAuthPage();

    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fillInput("邮箱", "person@example.com");
    fillInput("验证码", "123456");
    fillInput("密码", "new-password");
    fireEvent.click(screen.getByRole("checkbox"));

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    fireEvent(document, new Event("visibilitychange"));

    fireEvent.click(screen.getByRole("tab", { name: "登录" }));
    expect(screen.getByRole("tab", { name: "登录" })).toHaveAttribute("aria-selected", "true");

    writeAuthFormDraft({
      mode: "register",
      loginMethod: "password",
      email: "person@example.com",
      password: "new-password",
      code: "123456",
      termsAccepted: true,
      sendCooldownEndsAt: null,
    });

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
    fireEvent(document, new Event("visibilitychange"));

    expect(screen.getByRole("tab", { name: "注册" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText(/^邮箱/)).toHaveValue("person@example.com");
    expect(screen.getByLabelText(/^验证码/)).toHaveValue("123456");
    expect(screen.getByLabelText(/^密码/)).toHaveValue("new-password");
    expect(screen.getByRole("checkbox")).toBeChecked();
  });

  it("clears the saved draft after a successful login", async () => {
    renderAuthPage();

    fillInput("邮箱", "person@example.com");
    fillInput("密码", "correct-password");
    await waitFor(() => expect(screen.getByRole("button", { name: "登录" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() =>
      expect(loginWithPasswordMock).toHaveBeenCalledWith({
        email: "person@example.com",
        password: "correct-password",
        captchaToken: null,
      }),
    );
    await waitFor(() => expect(refreshAuth).toHaveBeenCalledTimes(1));
    expect(sessionStorage.getItem("kig-auth-form-draft")).toBeNull();
  });

  it("sends registration code and submits code registration", async () => {
    renderAuthPage();

    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fillInput("邮箱", "person@example.com");
    fillInput("验证码", "123456");
    fillInput("密码", "new-password");
    await waitFor(() => expect(screen.getByRole("button", { name: "发送验证码" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));

    await waitFor(() =>
      expect(sendAuthCodeMock).toHaveBeenCalledWith({
        email: "person@example.com",
        purpose: "register",
        captchaToken: null,
      }),
    );

    const registerButton = screen.getByRole("button", { name: "注册" });
    expect(registerButton).toBeDisabled();
    expect(registerAccountMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(registerButton).toBeEnabled();
    fireEvent.click(registerButton);

    await waitFor(() =>
      expect(registerAccountMock).toHaveBeenCalledWith({
        email: "person@example.com",
        code: "123456",
        password: "new-password",
        termsAccepted: true,
        termsVersion: "server-terms-v2",
        captchaToken: null,
      }),
    );
    await waitFor(() => expect(refreshAuth).toHaveBeenCalledTimes(1));
  });

  it("explains and blocks a registration password shorter than eight characters", async () => {
    renderAuthPage();

    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fillInput("邮箱", "person@example.com");
    fillInput("验证码", "123456");
    fillInput("密码", "123456");
    fireEvent.click(screen.getByRole("checkbox"));

    expect(screen.getByText("密码至少需要 8 个字符。")).toBeVisible();
    expect(screen.getByRole("button", { name: "注册" })).toBeDisabled();
    expect(registerAccountMock).not.toHaveBeenCalled();
  });

  it("refreshes the terms version and asks the user to review it when registration is stale", async () => {
    await i18n.changeLanguage("en");
    registerAccountMock.mockRejectedValue(new ApiError("stale terms", 400, "terms_version_outdated"));
    renderAuthPage();

    fireEvent.click(screen.getByRole("tab", { name: "Register" }));
    fillInput("Email", "person@example.com");
    fillInput("Verification Code", "123456");
    fillInput("Password", "new-password");
    fireEvent.click(screen.getByRole("checkbox"));
    const registerButton = screen.getByRole("button", { name: "Register" });
    await waitFor(() => expect(registerButton).toBeEnabled());
    fireEvent.click(registerButton);

    await waitFor(() => expect(registerAccountMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("The terms were updated. Please review them and accept again before registering.")).toBeVisible());
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    await waitFor(() => expect(getCurrentTermsVersionMock).toHaveBeenCalledTimes(2));
  });

  it("shows agreement controls only while registering and resets acceptance when leaving registration", async () => {
    await i18n.changeLanguage("en");
    renderAuthPage();

    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Register" }));
    const agreement = screen.getByRole("checkbox");
    fireEvent.click(agreement);
    expect(agreement).toBeChecked();

    fireEvent.click(screen.getByRole("tab", { name: "Log in" }));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Register" }));
    expect(screen.getByRole("checkbox")).not.toBeChecked();
  });

  it("opens terms and privacy documents from registration agreement links", async () => {
    await i18n.changeLanguage("en");
    renderAuthPage();

    fireEvent.click(screen.getByRole("tab", { name: "Register" }));
    fireEvent.click(screen.getByRole("button", { name: "Terms of Service" }));
    expect(screen.getByRole("heading", { name: "Terms of Service" })).toBeVisible();

    cleanup();
    renderAuthPage();
    fireEvent.click(screen.getByRole("tab", { name: "Register" }));
    fireEvent.click(screen.getByRole("button", { name: "Privacy Notice" }));
    expect(screen.getByRole("heading", { name: "Privacy Notice" })).toBeVisible();
  });

  it("supports code login and password reset without tier wording", async () => {
    renderAuthPage();

    fireEvent.click(screen.getByRole("button", { name: "验证码登录" }));
    fillInput("邮箱", "person@example.com");
    fillInput("验证码", "654321");
    await waitFor(() => expect(screen.getByRole("button", { name: "登录" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() =>
      expect(loginWithCodeMock).toHaveBeenCalledWith({
        email: "person@example.com",
        code: "654321",
        captchaToken: null,
      }),
    );

    fireEvent.click(screen.getByRole("tab", { name: "重置密码" }));
    fillInput("邮箱", "person@example.com");
    fillInput("验证码", "789012");
    fillInput("密码", "reset-password");
    await waitFor(() => expect(screen.getByRole("button", { name: "重置密码" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "重置密码" }));

    await waitFor(() =>
      expect(resetPasswordMock).toHaveBeenCalledWith({
        email: "person@example.com",
        code: "789012",
        password: "reset-password",
        captchaToken: null,
      }),
    );

    expect(screen.queryByText(/premium|normal|advanced|高级|普通|付费/i)).toBeNull();
  });

  it("passes captcha token when verification is enabled", async () => {
    getAuthCaptchaConfigMock.mockResolvedValue({ enabled: true, site_key: "site-key" });
    renderAuthPage();

    await screen.findByRole("button", { name: "captcha-widget-0" });
    fillInput("邮箱", "person@example.com");
    fillInput("密码", "correct-password");
    fireEvent.click(screen.getByRole("button", { name: "captcha-widget-0" }));
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() =>
      expect(loginWithPasswordMock).toHaveBeenCalledWith({
        email: "person@example.com",
        password: "correct-password",
        captchaToken: "captcha-token",
      }),
    );
    await screen.findByRole("button", { name: "captcha-widget-1" });
  });

  it.each([
    ["email_already_registered", "This email is already registered. Try signing in instead."],
    ["account_not_found", "No account was found for this email."],
    ["invalid_email_or_password", "The email or password is incorrect."],
    ["account_unavailable", "This account is unavailable. Please contact support."],
  ])("shows a localized message for %s", async (detail, message) => {
    await i18n.changeLanguage("en");
    loginWithPasswordMock.mockRejectedValue(new ApiError("raw server message", 401, detail));
    renderAuthPage();

    fillInput("Email", "person@example.com");
    fillInput("Password", "wrong-password");
    await waitFor(() => expect(screen.getByRole("button", { name: "Log in" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(screen.getByText(message)).toBeVisible());
    expect(screen.queryByText(detail)).not.toBeInTheDocument();
    expect(screen.queryByText("raw server message")).not.toBeInTheDocument();
  });

  it("shows a localized message for an invalid verification code", async () => {
    await i18n.changeLanguage("en");
    loginWithCodeMock.mockRejectedValue(new ApiError("raw server message", 401, "verification_code_invalid"));
    renderAuthPage();

    fireEvent.click(screen.getByRole("button", { name: "Verification Code" }));
    fillInput("Email", "person@example.com");
    fillInput("Verification Code", "000000");
    await waitFor(() => expect(screen.getByRole("button", { name: "Log in" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(screen.getByText("That verification code is invalid or expired.")).toBeVisible());
    expect(screen.queryByText("verification_code_invalid")).not.toBeInTheDocument();
  });

  it("uses a generic localized fallback for unknown ApiError details", async () => {
    await i18n.changeLanguage("en");
    loginWithPasswordMock.mockRejectedValue(new ApiError("API request failed: 422", 422, "unknown_internal_code"));
    renderAuthPage();

    fillInput("Email", "person@example.com");
    fillInput("Password", "wrong-password");
    await waitFor(() => expect(screen.getByRole("button", { name: "Log in" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(screen.getByText("Request failed. Please try again.")).toBeVisible());
    expect(screen.queryByText("unknown_internal_code")).not.toBeInTheDocument();
    expect(screen.queryByText("API request failed: 422")).not.toBeInTheDocument();
  });

  it("uses the generic fallback for prototype-key ApiError details", async () => {
    await i18n.changeLanguage("en");
    loginWithPasswordMock.mockRejectedValue(new ApiError("API request failed", 400, "__proto__"));
    renderAuthPage();

    fillInput("Email", "person@example.com");
    fillInput("Password", "wrong-password");
    await waitFor(() => expect(screen.getByRole("button", { name: "Log in" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(screen.getByText("Request failed. Please try again.")).toBeVisible());
    expect(screen.queryByText("__proto__")).not.toBeInTheDocument();
  });
});
