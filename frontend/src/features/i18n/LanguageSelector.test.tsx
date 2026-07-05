import { ThemeProvider } from "@mui/material/styles";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../app/theme";
import i18n from "../../i18n";
import { DEFAULT_LOCALE, LOCALE_STORAGE_KEY } from "../../i18n/locales";
import { LanguageSelector } from "./LanguageSelector";

function renderSelector({ compact = false }: { compact?: boolean } = {}) {
  render(
    <ThemeProvider theme={kigTheme}>
      <LanguageSelector compact={compact} />
    </ThemeProvider>,
  );
}

describe("LanguageSelector", () => {
  beforeEach(async () => {
    window.localStorage.clear();
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  afterEach(async () => {
    cleanup();
    window.localStorage.clear();
    vi.restoreAllMocks();
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it("renders all supported language choices", () => {
    renderSelector();

    const button = screen.getByRole("button", { name: "语言 中文" });
    expect(button).toHaveTextContent("中文");
    expect(button).not.toHaveTextContent("语言");
    fireEvent.click(button);

    expect(screen.getByRole("menuitem", { name: "中文" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "English" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "日本語" })).toBeInTheDocument();
  });

  it("persists selected language and updates its accessible label", async () => {
    renderSelector();

    fireEvent.click(screen.getByRole("button", { name: "语言 中文" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "English" }));

    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
    const button = await screen.findByRole("button", { name: "Language English" });
    expect(button).toHaveTextContent("English");
    expect(button).not.toHaveTextContent("Language");
  });

  it("persists Japanese selections", async () => {
    renderSelector();

    fireEvent.click(screen.getByRole("button", { name: "语言 中文" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "日本語" }));

    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("ja");
    const button = await screen.findByRole("button", { name: "言語 日本語" });
    expect(button).toHaveTextContent("日本語");
    expect(button).not.toHaveTextContent("言語");
  });

  it("renders compact visible text with a full accessible label", () => {
    renderSelector({ compact: true });

    const button = screen.getByRole("button", { name: "语言 中文" });

    expect(button).toHaveTextContent("中文");
    expect(button).not.toHaveTextContent("语言 中文");
  });

  it("marks the trigger as a controlled menu button", () => {
    renderSelector();

    const button = screen.getByRole("button", { name: "语言 中文" });

    expect(button).toHaveAttribute("aria-haspopup", "menu");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).not.toHaveAttribute("aria-controls");

    fireEvent.click(button);

    expect(button).toHaveAttribute("aria-expanded", "true");
    const menu = screen.getByRole("menu");
    expect(button).toHaveAttribute("aria-controls", menu.id);
  });
});
