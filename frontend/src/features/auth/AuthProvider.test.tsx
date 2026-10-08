import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import type { CurrentUser } from "../../api/client";
import { updateCachedCurrentUserForActiveSession } from "./AuthProvider";

const currentUser: CurrentUser = {
  id: "user-1",
  email: "person@example.com",
  role: "user",
  is_banned: false,
  daily_generation_remaining: 10,
  daily_generation_reset_at: null,
  terms_version: "server-terms-v2",
  terms_accepted_at: null,
  generation_quality_improvement_consent: false,
  generation_quality_improvement_consent_updated_at: null,
};

describe("updateCachedCurrentUserForActiveSession", () => {
  it("keeps a confirmed update after the dialog has closed", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(["auth", "me"], currentUser);

    updateCachedCurrentUserForActiveSession(queryClient, {
      ...currentUser,
      generation_quality_improvement_consent: true,
    }, 1, 1);

    expect(queryClient.getQueryData<CurrentUser>(["auth", "me"])?.generation_quality_improvement_consent).toBe(true);
  });

  it("does not repopulate the auth cache after logout", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(["auth", "me"], null);

    updateCachedCurrentUserForActiveSession(queryClient, {
      ...currentUser,
      generation_quality_improvement_consent: true,
    }, 1, 1);

    expect(queryClient.getQueryData(["auth", "me"])).toBeNull();
  });

  it("ignores a delayed privacy save from an earlier session after the same user signs back in", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(["auth", "me"], currentUser);

    updateCachedCurrentUserForActiveSession(queryClient, {
      ...currentUser,
      generation_quality_improvement_consent: true,
    }, 4, 5);

    expect(queryClient.getQueryData<CurrentUser>(["auth", "me"])?.generation_quality_improvement_consent).toBe(false);
  });
});
