import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CurrentUser } from "../../api/client";
import { updatePrivacyPreferences } from "../../api/client";
import i18n from "../../i18n";
import { DEFAULT_LOCALE } from "../../i18n/locales";
import { PrivacySettingsDialog } from "./PrivacySettingsDialog";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, updatePrivacyPreferences: vi.fn() };
});

const updatePrivacyPreferencesMock = vi.mocked(updatePrivacyPreferences);

const currentUser: CurrentUser = {
  id: "user-1",
  email: "test@example.com",
  role: "user",
  is_banned: false,
  daily_generation_remaining: 18,
  daily_generation_reset_at: null,
  terms_version: "2026-07-01",
  terms_accepted_at: "2026-07-01T00:00:00Z",
  generation_quality_improvement_consent: false,
  generation_quality_improvement_consent_updated_at: null,
};

describe("PrivacySettingsDialog", () => {
  beforeEach(async () => {
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  afterEach(async () => {
    cleanup();
    vi.clearAllMocks();
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it("shows the server-confirmed quality-improvement consent as off by default", () => {
    render(<PrivacySettingsDialog authSessionEpoch={1} currentUser={currentUser} onClose={vi.fn()} onUpdated={vi.fn()} />);

    expect(screen.getByRole("switch")).not.toBeChecked();
  });

  it("saves a changed consent value and reports the updated user", async () => {
    const onUpdated = vi.fn();
    const updatedUser = { ...currentUser, generation_quality_improvement_consent: true };
    updatePrivacyPreferencesMock.mockResolvedValue(updatedUser);
    render(<PrivacySettingsDialog authSessionEpoch={1} currentUser={currentUser} onClose={vi.fn()} onUpdated={onUpdated} />);

    fireEvent.click(screen.getByRole("switch"));

    await waitFor(() => expect(updatePrivacyPreferencesMock).toHaveBeenCalledWith(true));
    await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(updatedUser, 1));
    expect(screen.getByRole("switch")).toBeChecked();
  });

  it("reports a server-confirmed save after the dialog closes", async () => {
    const onUpdated = vi.fn();
    let resolveUpdate!: (user: CurrentUser) => void;
    updatePrivacyPreferencesMock.mockReturnValue(new Promise((resolve) => {
      resolveUpdate = resolve;
    }));
    const { unmount } = render(<PrivacySettingsDialog authSessionEpoch={1} currentUser={currentUser} onClose={vi.fn()} onUpdated={onUpdated} />);

    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(updatePrivacyPreferencesMock).toHaveBeenCalledWith(true));
    unmount();

    await act(async () => {
      resolveUpdate({ ...currentUser, generation_quality_improvement_consent: true });
    });

    expect(onUpdated).toHaveBeenCalledWith({ ...currentUser, generation_quality_improvement_consent: true }, 1);
  });

  it("keeps the confirmed value and shows an error when saving fails", async () => {
    updatePrivacyPreferencesMock.mockRejectedValue(new Error("network error"));
    render(<PrivacySettingsDialog authSessionEpoch={1} currentUser={currentUser} onClose={vi.fn()} onUpdated={vi.fn()} />);

    fireEvent.click(screen.getByRole("switch"));

    await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
    expect(screen.getByRole("switch")).not.toBeChecked();
  });
});
