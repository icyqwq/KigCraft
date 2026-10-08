import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { DEFAULT_LOCALE } from "../../i18n/locales";
import { COOKIE_NOTICE_DISMISSED_STORAGE_KEY, CookieNotice } from "./CookieNotice";

describe("CookieNotice", () => {
  beforeEach(async () => {
    localStorage.clear();
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  afterEach(async () => {
    cleanup();
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it("dismisses the necessary-Cookie notice using only its acknowledgement key", () => {
    render(<CookieNotice onViewCookieDocument={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "知道了" }));

    expect(localStorage.getItem(COOKIE_NOTICE_DISMISSED_STORAGE_KEY)).toBe("1");
    expect(screen.queryByText("我们使用必要 Cookie 以保持登录状态、保护账户安全并提供基础功能。"))
      .not.toBeInTheDocument();
  });

  it("does not render again after acknowledgement and opens the Cookie document on request", () => {
    const onViewCookieDocument = vi.fn();
    const { unmount } = render(<CookieNotice onViewCookieDocument={onViewCookieDocument} />);

    fireEvent.click(screen.getByRole("button", { name: "查看 Cookie 说明" }));
    expect(onViewCookieDocument).toHaveBeenCalledTimes(1);

    localStorage.setItem(COOKIE_NOTICE_DISMISSED_STORAGE_KEY, "1");
    unmount();
    render(<CookieNotice onViewCookieDocument={onViewCookieDocument} />);
    expect(screen.queryByRole("button", { name: "知道了" })).not.toBeInTheDocument();
  });
});
