import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { DEFAULT_LOCALE } from "../../i18n/locales";
import en from "../../i18n/resources/en";
import ja from "../../i18n/resources/ja";
import zhCN from "../../i18n/resources/zh-CN";
import { LegalDocumentsDialog } from "./LegalDocumentsDialog";

describe("LegalDocumentsDialog", () => {
  beforeEach(async () => {
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  afterEach(async () => {
    cleanup();
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it("shows structured privacy content with the operator contact", () => {
    render(<LegalDocumentsDialog document="privacy" onClose={vi.fn()} open />);

    expect(screen.getByRole("heading", { name: "隐私说明" })).toBeVisible();
    expect(screen.getByText(/本服务由 KigCraft 运营。隐私相关问题请联系 icyqwq@gmail\.com。/)).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Cookie 说明" })).not.toBeInTheDocument();
  });

  it("switches between terms, privacy, and Cookie documents", () => {
    render(<LegalDocumentsDialog document="terms" onClose={vi.fn()} open />);

    expect(screen.getByRole("heading", { name: "用户服务协议" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Cookie 说明" }));
    expect(screen.getByRole("heading", { name: "Cookie 说明" })).toBeVisible();
  });
  it("provides a complete agreement and operator contact in every locale", () => {
    const locales = [zhCN, en, ja];

    for (const locale of locales) {
      expect(locale.legal.documents.terms.sections.length).toBeGreaterThanOrEqual(12);

      const privacyText = locale.legal.documents.privacy.sections
        .flatMap((section) => [section.heading, ...section.paragraphs])
        .join(" ");
      expect(privacyText).toContain("KigCraft");
      expect(privacyText).toContain("icyqwq@gmail.com");
    }
  });
});
