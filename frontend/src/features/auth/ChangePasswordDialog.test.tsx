import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, changePassword } from "../../api/client";
import i18n from "../../i18n";
import { DEFAULT_LOCALE } from "../../i18n/locales";
import { ChangePasswordDialog } from "./ChangePasswordDialog";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, changePassword: vi.fn() };
});

const changePasswordMock = vi.mocked(changePassword);

function fillInput(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(new RegExp(`^${label}`)), { target: { value } });
}

describe("ChangePasswordDialog", () => {
  beforeEach(async () => {
    await i18n.changeLanguage(DEFAULT_LOCALE);
    changePasswordMock.mockReset();
  });

  afterEach(async () => {
    cleanup();
    vi.clearAllMocks();
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it("submits current and new password when confirmation matches", async () => {
    changePasswordMock.mockResolvedValue({ ok: true });
    render(<ChangePasswordDialog onClose={vi.fn()} />);

    fillInput("当前密码", "OldPassword7!");
    fillInput("新密码", "NewPassword7!");
    fillInput("确认新密码", "NewPassword7!");
    fireEvent.click(screen.getByRole("button", { name: "保存新密码" }));

    await waitFor(() =>
      expect(changePasswordMock).toHaveBeenCalledWith({
        currentPassword: "OldPassword7!",
        newPassword: "NewPassword7!",
      }),
    );
    expect(await screen.findByText("密码已更新。其他设备上的登录已失效。")).toBeVisible();
  });

  it("shows a mismatch error without calling the API", async () => {
    render(<ChangePasswordDialog onClose={vi.fn()} />);

    fillInput("当前密码", "OldPassword7!");
    fillInput("新密码", "NewPassword7!");
    fillInput("确认新密码", "Different7!");
    fireEvent.click(screen.getByRole("button", { name: "保存新密码" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("两次输入的新密码不一致。");
    expect(changePasswordMock).not.toHaveBeenCalled();
  });

  it("maps invalid current password errors", async () => {
    changePasswordMock.mockRejectedValue(new ApiError("bad password", 401, "invalid_current_password"));
    render(<ChangePasswordDialog onClose={vi.fn()} />);

    fillInput("当前密码", "WrongPassword7!");
    fillInput("新密码", "NewPassword7!");
    fillInput("确认新密码", "NewPassword7!");
    fireEvent.click(screen.getByRole("button", { name: "保存新密码" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("当前密码不正确。");
  });
});
