import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../app/theme";
import i18n from "../../i18n";
import { DEFAULT_LOCALE } from "../../i18n/locales";
import { AuthDialog } from "./AuthDialog";
import { useAuth } from "./AuthProvider";

vi.mock("./AuthProvider", () => ({
  useAuth: vi.fn(),
}));

const useAuthMock = vi.mocked(useAuth);

function renderAuthDialog(onClose = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });

  return render(
    <ThemeProvider theme={kigTheme}>
      <QueryClientProvider client={client}>
        <AuthDialog open onAuthenticated={vi.fn()} onClose={onClose} />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

describe("AuthDialog", () => {
  beforeEach(async () => {
    await i18n.changeLanguage(DEFAULT_LOCALE);
    useAuthMock.mockReturnValue({
      currentUser: null,
      isAdmin: false,
      isLoading: false,
      logout: vi.fn(),
      refreshAuth: vi.fn().mockResolvedValue(undefined),
    });
  });

  afterEach(async () => {
    cleanup();
    await i18n.changeLanguage(DEFAULT_LOCALE);
    vi.clearAllMocks();
  });

  it("does not close when interacting with registration controls", () => {
    const onClose = vi.fn();
    renderAuthDialog(onClose);

    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fireEvent.click(screen.getByRole("button", { name: "发送验证码" }));

    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes when clicking the backdrop", () => {
    const onClose = vi.fn();
    const { container } = renderAuthDialog(onClose);
    const backdrop = container.firstElementChild as HTMLElement;

    fireEvent.mouseDown(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
