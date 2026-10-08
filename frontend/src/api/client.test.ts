import { afterEach, describe, expect, it, vi } from "vitest";

import {
  acceptGenerationCandidate,
  activateCodexProfile,
  adjustAdminUserDailyGenerationQuota,
  analyzeReferenceDetails,
  cancelCodexProfileDeviceAuth,
  createCodexProfile,
  deleteCodexProfile,
  createGenerationJob,
  createLocalRevisionJob,
  getCodexProfileDeviceAuth,
  getCodexUpdateStatus,
  getAuthCaptchaConfig,
  getCurrentTermsVersion,
  getCurrentUser,
  getAuditSummary,
  getGenerationEvents,
  getGenerationJob,
  listAdminUsers,
  resetAdminUserDailyGenerationQuota,
  resetDailyGenerationQuota,
  revokeAdminUserSessions,
  listGenerationJobs,
  listAlbumItems,
  listAdminUsersPage,
  importCodexProfileCredentials,
  listCodexProfiles,
  saveAlbumImageFile,
  saveAlbumItem,
  sendAuthCode,
  startCodexDeviceAuth,
  startCodexProfileDeviceAuth,
  uploadReferenceFile,
  updateAdminUserBan,
  updateAdminUserDailyGenerationLimit,
  updateAdminUserRole,
  registerAccount,
  changePassword,
  updatePrivacyPreferences,
  updateQuotaPolicy,
} from "./client";

function stubFetchJson(body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("generation API compatibility", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("normalizes a legacy generation job response", async () => {
    stubFetchJson({
      id: "job-a",
      project_id: "session-a",
      status: "rendering",
      progress: 42,
      outputs: [
        {
          index: 0,
          object_key: "https://cdn.example/job-a-0.png",
          width: 512,
          height: 768,
        },
      ],
    });

    await expect(getGenerationJob("job-a")).resolves.toEqual({
      id: "job-a",
      character_session_id: "session-a",
      generation_mode: "front_design",
      expected_output_count: 1,
      status: "rendering",
      progress: 42,
      queue_position: null,
      phase_label: "rendering",
      provider: "legacy",
      accepted_output_index: null,
      token_usage: null,
      outputs: [
        {
          index: 0,
          object_key: "https://cdn.example/job-a-0.png",
          image_url: "https://cdn.example/job-a-0.png",
          width: 512,
          height: 768,
        },
      ],
    });
  });

  it("accepts a generated candidate with the backend payload shape", async () => {
    const fetchMock = stubFetchJson({
      id: "job-a",
      character_session_id: "session-a",
      status: "accepted",
      progress: 100,
      queue_position: null,
      phase_label: "accepted",
      provider: "codex_bridge",
      accepted_output_index: 2,
      outputs: [
        {
          index: 2,
          object_key: "codex/session-a/job-a/outputs/candidate-2.webp",
          image_url: "/api/generated/session-a/job-a/outputs/candidate-2.webp",
          width: 2048,
          height: 1536,
        },
      ],
    });

    await expect(acceptGenerationCandidate("job-a", 2)).resolves.toMatchObject({
      status: "accepted",
      accepted_output_index: 2,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/generation/jobs/job-a/accept",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ output_index: 2 }),
      }),
    );
  });

  it("normalizes legacy generation events", async () => {
    stubFetchJson([
      { type: "queued", progress: 0 },
      { type: "rendering", progress: 50 },
    ]);

    await expect(getGenerationEvents("job-a")).resolves.toEqual([
      {
        sequence: 1,
        type: "queued",
        progress: 0,
        message: "queued",
        created_at: "",
        payload: {},
      },
      {
        sequence: 2,
        type: "rendering",
        progress: 50,
        message: "rendering",
        created_at: "",
        payload: {},
      },
    ]);
  });

  it("rejects malformed future-looking generation events instead of falling back to legacy", async () => {
    stubFetchJson([
      {
        sequence: 1,
        type: "queued",
        progress: 0,
        message: "queued",
        created_at: 123,
      },
    ]);

    await expect(getGenerationEvents("job-a")).rejects.toThrow();
  });

  it("parses account generation history", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({
      jobs: [{
        id: "job-1",
        character_session_id: "session-1",
        generation_mode: "front_design",
        expected_output_count: 1,
        status: "succeeded",
        progress: 100,
        queue_position: null,
        phase_label: "done",
        provider: "codex",
        accepted_output_index: null,
        token_usage: null,
        outputs: [],
      }],
    })));

    await expect(
      listGenerationJobs({ status: "succeeded", generationMode: "front_design" }),
    ).resolves.toEqual([
      expect.objectContaining({
        id: "job-1",
        generation_mode: "front_design",
        status: "succeeded",
      }),
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/generation/jobs?status=succeeded&generation_mode=front_design",
      undefined,
    );
  });

  it("analyzes reference details with the backend payload shape", async () => {
    const fetchMock = stubFetchJson({
      analysis_id: "analysis-a",
      features: [
        {
          id: "feature-user-requirement",
          kind: "requirement",
          label: "用户要求",
          description: "保留黑色 X 发夹、长直发和委屈表情。",
        },
        {
          id: "feature-hair",
          kind: "hair",
          label: "Hair",
          description: "Long light blue hair",
        },
      ],
      crops: [
        {
          id: "crop-headwear",
          kind: "headwear",
          description: "Left black X clip",
          source_reference_key: "front:references/upload-a/front.webp",
          bbox: { x: 0.1, y: 0.1, width: 0.3, height: 0.3 },
          object_key: "references/analysis-a/detail-1.webp",
          image_url: "/api/references/references/analysis-a/detail-1.webp",
        },
      ],
      warnings: [],
    });

    await expect(
      analyzeReferenceDetails({
        characterSessionId: "session-a",
        freeText: "keep hair",
        locale: "zh-CN",
        requirementIds: [],
        referenceKeys: ["front:references/upload-a/front.webp"],
        referenceDescriptions: [
          {
            referenceKey: "front:references/upload-a/front.webp",
            description: "front reference",
          },
        ],
      }),
    ).resolves.toEqual({
      analysisId: "analysis-a",
      features: [
        {
          id: "feature-user-requirement",
          kind: "requirement",
          label: "用户要求",
          description: "保留黑色 X 发夹、长直发和委屈表情。",
        },
        {
          id: "feature-hair",
          kind: "hair",
          label: "Hair",
          description: "Long light blue hair",
        },
      ],
      crops: [
        {
          id: "crop-headwear",
          kind: "headwear",
          description: "Left black X clip",
          sourceReferenceKey: "front:references/upload-a/front.webp",
          bbox: { x: 0.1, y: 0.1, width: 0.3, height: 0.3 },
          objectKey: "references/analysis-a/detail-1.webp",
          imageUrl: "/api/references/references/analysis-a/detail-1.webp",
        },
      ],
      warnings: [],
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/generation/detail-analysis",
      expect.objectContaining({
        method: "POST",
      }),
    );
    const [, requestInit] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(requestInit?.body))).toEqual({
      character_session_id: "session-a",
      free_text: "keep hair",
      locale: "zh-CN",
      requirement_ids: [],
      reference_keys: ["front:references/upload-a/front.webp"],
      reference_descriptions: [
        {
          reference_key: "front:references/upload-a/front.webp",
          description: "front reference",
        },
      ],
    });
  });

  it("sends locale with detail analysis requests", async () => {
    const fetchMock = stubFetchJson({
      analysis_id: "analysis-a",
      features: [],
      crops: [],
      warnings: [],
    });

    await analyzeReferenceDetails({
      characterSessionId: "session-a",
      freeText: "保留猫耳",
      locale: "ja",
      referenceDescriptions: [],
      referenceKeys: ["front:references/session/front.webp"],
      requirementIds: [],
    });

    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toMatchObject({
      locale: "ja",
    });
  });

  it("sends detail analysis requests with an abort signal", async () => {
    const fetchMock = stubFetchJson({
      analysis_id: "analysis-a",
      features: [],
      crops: [],
      warnings: [],
    });

    await analyzeReferenceDetails({
      characterSessionId: "session-a",
      freeText: "keep hair",
      locale: "zh-CN",
      referenceDescriptions: [],
      referenceKeys: ["front:references/upload-a/front.webp"],
      requirementIds: [],
    });

    const [, requestInit] = fetchMock.mock.calls[0];
    expect(requestInit?.signal).toBeInstanceOf(AbortSignal);
  });

  it("parses ears detail analysis response kinds", async () => {
    stubFetchJson({
      analysis_id: "analysis-ears",
      features: [
        {
          id: "feature-ears",
          kind: "ears",
          label: "Ears",
          description: "Soft cat ears",
        },
      ],
      crops: [
        {
          id: "crop-ears",
          kind: "ears",
          description: "Cat ears close-up",
          source_reference_key: "front:references/upload-a/front.webp",
          bbox: { x: 0.25, y: 0.05, width: 0.4, height: 0.25 },
          object_key: "references/analysis-ears/detail-ears.webp",
          image_url: "/api/references/references/analysis-ears/detail-ears.webp",
        },
      ],
      warnings: [],
    });

    await expect(
      analyzeReferenceDetails({
        characterSessionId: "session-a",
        freeText: "keep cat ears",
        locale: "zh-CN",
        referenceDescriptions: [],
        referenceKeys: ["front:references/upload-a/front.webp"],
        requirementIds: [],
      }),
    ).resolves.toMatchObject({
      features: [expect.objectContaining({ kind: "ears" })],
      crops: [expect.objectContaining({ kind: "ears" })],
    });
  });

  it("creates generation jobs with detail lock payloads", async () => {
    const fetchMock = stubFetchJson({
      id: "job-a",
      character_session_id: "session-a",
      status: "queued",
      progress: 0,
      queue_position: null,
      phase_label: "queued",
      provider: "codex_bridge",
      outputs: [],
    });

    await expect(
      createGenerationJob({
        characterSessionId: "session-a",
        freeText: "make it precise",
        requirementIds: [],
        referenceKeys: ["detail:references/analysis-a/detail-1.webp"],
        detailLock: {
          sourceAnalysisId: "analysis-a",
          userNote: "keep user edits",
          features: [
            {
              id: "feature-hair",
              kind: "hair",
              label: "Hair",
              description: "Long hair",
            },
          ],
          crops: [
            {
              referenceKey: "detail:references/analysis-a/detail-1.webp",
              kind: "hair",
              description: "Hair crop",
            },
          ],
        },
      }),
    ).resolves.toMatchObject({ id: "job-a" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/generation/jobs",
      expect.objectContaining({
        method: "POST",
      }),
    );
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      character_session_id: "session-a",
      detail_lock: {
        source_analysis_id: "analysis-a",
        user_note: "keep user edits",
        features: [
          {
            id: "feature-hair",
            kind: "hair",
            label: "Hair",
            description: "Long hair",
          },
        ],
        crops: [
          {
            reference_key: "detail:references/analysis-a/detail-1.webp",
            kind: "hair",
            description: "Hair crop",
          },
        ],
      },
      free_text: "make it precise",
      generation_mode: "front_design",
      locale: "zh-CN",
      reference_descriptions: [],
      requirement_ids: [],
      reference_keys: ["detail:references/analysis-a/detail-1.webp"],
    });
  });

  it("sends locale with generation job requests", async () => {
    const fetchMock = stubFetchJson({
      id: "job-a",
      character_session_id: "session-a",
      status: "queued",
      progress: 0,
      queue_position: null,
      phase_label: "queued",
      provider: "codex_bridge",
      outputs: [],
    });

    await createGenerationJob({
      characterSessionId: "session-a",
      freeText: "",
      generationMode: "front_design",
      locale: "en",
      referenceDescriptions: [],
      referenceKeys: ["front:references/session/front.webp"],
      requirementIds: [],
    });

    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toMatchObject({
      locale: "en",
    });
  });

  it("normalizes uploaded local revision references before multipart submission", async () => {
    const fetchMock = stubFetchJson({
      id: "job-local",
      character_session_id: "session-a",
      generation_mode: "front_local_revision",
      status: "queued",
      progress: 0,
      queue_position: null,
      phase_label: "queued",
      provider: "codex",
      outputs: [],
    });
    const originalImage = globalThis.Image;
    const originalGetContext = HTMLCanvasElement.prototype.getContext;
    const originalToBlob = HTMLCanvasElement.prototype.toBlob;
    class ImageStub {
      onerror: (() => void) | null = null;
      onload: (() => void) | null = null;
      naturalHeight = 900;
      naturalWidth = 1200;
      set src(_value: string) {
        this.onload?.();
      }
    }
    vi.stubGlobal("Image", ImageStub);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:reference"),
      revokeObjectURL: vi.fn(),
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: vi.fn(() => ({
        drawImage: vi.fn(),
        fillRect: vi.fn(),
        fillStyle: "",
      })),
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "toBlob", {
      configurable: true,
      value: vi.fn((callback: BlobCallback) => callback(new Blob(["compressed"], { type: "image/jpeg" }))),
    });

    try {
      await createLocalRevisionJob({
        baseImageBlob: new Blob(["base"], { type: "image/png" }),
        editNote: "fix mouth",
        maskImageBlob: new Blob(["mask"], { type: "image/png" }),
        selectedReferenceKeys: [],
        uploadedReferences: [{ description: "mouth reference", file: new File(["raw"], "mouth.png", { type: "image/png" }) }],
      });

      const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
      const body = init.body as FormData;
      const referenceFile = body.get("reference_files") as File;
      expect(referenceFile).toBeInstanceOf(File);
      expect(referenceFile.name).toBe("mouth.jpg");
      expect(referenceFile.type).toBe("image/jpeg");
      expect(referenceFile.size).toBe("compressed".length);
    } finally {
      vi.stubGlobal("Image", originalImage);
      Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
        configurable: true,
        value: originalGetContext,
      });
      Object.defineProperty(HTMLCanvasElement.prototype, "toBlob", {
        configurable: true,
        value: originalToBlob,
      });
    }
  });

  it("creates detail lock payloads with a nullable source analysis id", async () => {
    const fetchMock = stubFetchJson({
      id: "job-a",
      character_session_id: "session-a",
      status: "queued",
      progress: 0,
      queue_position: null,
      phase_label: "queued",
      provider: "codex_bridge",
      outputs: [],
    });

    await createGenerationJob({
      characterSessionId: "session-a",
      freeText: "make it precise",
      requirementIds: [],
      referenceKeys: ["detail:references/analysis-a/detail-1.webp"],
      detailLock: {
        sourceAnalysisId: null,
        userNote: "keep user edits",
        features: [],
        crops: [
          {
            referenceKey: "detail:references/analysis-a/detail-1.webp",
            kind: "hair",
            description: "Hair crop",
          },
        ],
      },
    });

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toMatchObject({
      detail_lock: {
        source_analysis_id: null,
      },
      reference_keys: ["detail:references/analysis-a/detail-1.webp"],
    });
  });
});

describe("auth API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses current user and sends auth requests", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
      id: "user-1",
      email: "person@example.com",
      role: "user",
      is_banned: false,
    })));

    await expect(getCurrentUser()).resolves.toMatchObject({ email: "person@example.com", role: "user" });

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true, site_key: "site-key" })));
    await expect(getAuthCaptchaConfig()).resolves.toEqual({ enabled: true, site_key: "site-key" });

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true })));
    await sendAuthCode({
      email: "person@example.com",
      purpose: "register",
      captchaToken: "captcha-token",
    });

    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/auth/send-code",
      expect.objectContaining({ method: "POST" }),
    );
    const [, init] = fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toMatchObject({
      captcha_token: "captcha-token",
      email: "person@example.com",
      purpose: "register",
    });
  });

  it("sends terms acceptance on registration and persists the privacy preference", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const currentUser = {
      id: "user-1",
      email: "person@example.com",
      role: "user",
      is_banned: false,
      terms_version: "2026-07-11",
      terms_accepted_at: "2026-07-11T00:00:00Z",
      generation_quality_improvement_consent: true,
      generation_quality_improvement_consent_updated_at: "2026-07-11T00:00:00Z",
    };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(currentUser)));

    await registerAccount({
      email: "person@example.com",
      code: "123456",
      password: "secure-password",
      termsAccepted: true,
      termsVersion: "2026-07-11",
    });

    expect(JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string)).toMatchObject({
      terms_accepted: true,
      terms_version: "2026-07-11",
    });

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(currentUser)));
    await expect(updatePrivacyPreferences(true)).resolves.toMatchObject({
      generation_quality_improvement_consent: true,
    });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/auth/privacy-preferences",
      expect.objectContaining({ method: "PATCH" }),
    );
    expect(JSON.parse((fetchMock.mock.calls[1]?.[1] as RequestInit).body as string)).toEqual({
      generation_quality_improvement_consent: true,
    });
  });

  it("changes the signed-in user password", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true })));

    await expect(
      changePassword({ currentPassword: "OldPassword7!", newPassword: "NewPassword7!" }),
    ).resolves.toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/change-password",
      expect.objectContaining({ method: "POST" }),
    );
    expect(JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string)).toEqual({
      current_password: "OldPassword7!",
      new_password: "NewPassword7!",
    });
  });

  it("retrieves the current registration terms version from the backend", async () => {
    const fetchMock = stubFetchJson({ version: "server-terms-v2" });

    await expect(getCurrentTermsVersion()).resolves.toEqual({ version: "server-terms-v2" });
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/terms-version", undefined);
  });
});

describe("audit and album API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses admin users without account tier fields", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({
      users: [{
        id: "user-1",
        email: "managed@example.com",
        role: "user",
        is_banned: false,
        created_at: "2026-07-08T00:00:00Z",
        last_login_at: null,
        task_count: 2,
        call_count: 2,
        generation_count: 3,
        daily_generation_limit: 100,
        daily_generation_limit_override: 100,
        daily_generation_used: 12,
        daily_generation_remaining: 88,
        daily_generation_balance: 88,
        daily_generation_reset_at: "2026-07-10T06:00:00+08:00",
      }],
      total: 1,
      limit: 25,
      offset: 0,
      has_more: false,
      sort_by: "generation_count",
      sort_dir: "desc",
    })));

    const users = await listAdminUsers("managed");

    expect(users[0]).toMatchObject({
      daily_generation_limit: 100,
      daily_generation_limit_override: 100,
      daily_generation_remaining: 88,
      generation_count: 3,
      email: "managed@example.com",
      role: "user",
    });
    expect(JSON.stringify(users[0])).not.toMatch(/premium|normal/i);
    expect(fetchMock).toHaveBeenCalledWith("/api/admin/users?query=managed", undefined);

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
      users: [],
      total: 3,
      limit: 2,
      offset: 2,
      has_more: false,
      sort_by: "remaining_quota",
      sort_dir: "asc",
    })));

    await expect(
      listAdminUsersPage({
        query: "managed",
        limit: 2,
        offset: 2,
        sortBy: "remaining_quota",
        sortDir: "asc",
      }),
    ).resolves.toMatchObject({ total: 3, limit: 2, offset: 2, has_more: false });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/admin/users?query=managed&limit=2&offset=2&sort_by=remaining_quota&sort_dir=asc",
      undefined,
    );
  });

  it("updates an admin user role with an encoded id", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({
      id: "user/with space?%",
      email: "managed@example.com",
      role: "admin",
      is_banned: false,
      created_at: "2026-07-08T00:00:00Z",
      last_login_at: null,
      task_count: 2,
      call_count: 2,
    })));

    await expect(updateAdminUserRole("user/with space?%", "admin")).resolves.toMatchObject({ role: "admin" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/users/user%2Fwith%20space%3F%25/role",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ role: "admin" }),
      }),
    );
  });

  it("updates an admin user ban state with an encoded id", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({
      id: "user/with space?%",
      email: "managed@example.com",
      role: "user",
      is_banned: true,
      created_at: "2026-07-08T00:00:00Z",
      last_login_at: null,
      task_count: 2,
      call_count: 2,
    })));

    await expect(updateAdminUserBan("user/with space?%", true)).resolves.toMatchObject({ is_banned: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/users/user%2Fwith%20space%3F%25/ban",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ is_banned: true }),
      }),
    );
  });

  it("revokes admin user sessions with an encoded id", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ revoked_sessions: 3 })),
    );

    await expect(revokeAdminUserSessions("user/with space?%")).resolves.toEqual({ revoked_sessions: 3 });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/users/user%2Fwith%20space%3F%25/revoke-sessions",
      expect.objectContaining({
        method: "POST",
      }),
    );
  });

  it("resets daily generation quota", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ reset_count: 4, window_start_at: "2026-07-09T06:00:00+08:00" })),
    );

    await expect(resetDailyGenerationQuota()).resolves.toEqual({
      reset_count: 4,
      window_start_at: "2026-07-09T06:00:00+08:00",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/quota/daily-generation/reset",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("resets one user's daily generation quota with an encoded id", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ reset_count: 1, window_start_at: "2026-07-09T06:00:00+08:00" })),
    );

    await expect(resetAdminUserDailyGenerationQuota("user/with space?%")).resolves.toMatchObject({
      reset_count: 1,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/users/user%2Fwith%20space%3F%25/quota/daily-generation/reset",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("updates and clears a user's daily generation limit override", async () => {
    const updatedUser = {
      id: "user/with space?%",
      email: "managed@example.com",
      role: "user",
      is_banned: false,
      created_at: "2026-07-08T00:00:00Z",
      last_login_at: null,
      task_count: 2,
      call_count: 2,
      daily_generation_limit: 100,
      daily_generation_limit_override: 100,
      daily_generation_used: 0,
      daily_generation_remaining: 100,
      daily_generation_reset_at: "2026-07-10T06:00:00+08:00",
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(updatedUser)))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...updatedUser, daily_generation_limit_override: null })));

    await expect(updateAdminUserDailyGenerationLimit("user/with space?%", 100)).resolves.toMatchObject({
      daily_generation_limit_override: 100,
    });
    await expect(updateAdminUserDailyGenerationLimit("user/with space?%", null)).resolves.toMatchObject({
      daily_generation_limit_override: null,
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/admin/users/user%2Fwith%20space%3F%25/quota/daily-generation-limit",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ limit: 100 }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/admin/users/user%2Fwith%20space%3F%25/quota/daily-generation-limit",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ limit: null }),
      }),
    );
  });

  it("adjusts one user's daily generation balance with an encoded id", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({
      id: "user/with space?%",
      email: "managed@example.com",
      role: "user",
      is_banned: false,
      created_at: "2026-07-08T00:00:00Z",
      last_login_at: null,
      task_count: 2,
      call_count: 2,
      generation_count: 2,
      daily_generation_limit: 10,
      daily_generation_limit_override: null,
      daily_generation_used: 0,
      daily_generation_remaining: 25,
      daily_generation_balance: 25,
      daily_generation_reset_at: "2026-07-10T06:00:00+08:00",
    })));

    await expect(adjustAdminUserDailyGenerationQuota("user/with space?%", 5)).resolves.toMatchObject({
      daily_generation_balance: 25,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/users/user%2Fwith%20space%3F%25/quota/daily-generation/adjust",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ delta: 5 }),
      }),
    );
  });

  it("manages codex profiles without echoing imported credentials", async () => {
    const profile = {
      id: "account-a",
      name: "Account A",
      is_active: false,
      is_builtin: false,
      credential_files: [],
      created_at: "2026-07-09T00:00:00Z",
      updated_at: "2026-07-09T00:00:00Z",
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({
        profiles: [{
          id: "__default__",
          name: "默认账号",
          is_active: true,
          is_builtin: true,
          credential_files: ["auth.json"],
          created_at: "2026-07-09T00:00:00Z",
          updated_at: "2026-07-09T00:00:00Z",
        }, profile],
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify(profile)))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...profile, credential_files: ["auth.json"] })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...profile, is_active: true, credential_files: ["auth.json"] })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true })))
      .mockImplementation(async () => new Response(JSON.stringify({
        id: "session-1",
        profile_id: "account-a",
        status: "running",
        output: "Open browser",
        error: null,
        created_at: "2026-07-09T00:02:00Z",
        completed_at: null,
      })));

    await expect(listCodexProfiles()).resolves.toHaveLength(2);
    await expect(createCodexProfile({ id: "account-a", name: "Account A" })).resolves.toMatchObject({ id: "account-a" });

    const credentialText = "{\"token\":\"secret\"}";
    const imported = await importCodexProfileCredentials("account-a", { "auth.json": credentialText });
    expect(JSON.stringify(imported)).not.toContain("secret");
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/admin/codex-profiles/account-a/credentials",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ files: { "auth.json": credentialText } }),
      }),
    );

    await expect(activateCodexProfile("account-a")).resolves.toMatchObject({ is_active: true });
    await expect(deleteCodexProfile("account-a")).resolves.toEqual({ ok: true });
    await expect(startCodexProfileDeviceAuth("account-a")).resolves.toMatchObject({ status: "running" });
    await expect(getCodexProfileDeviceAuth("account-a", "session-1")).resolves.toMatchObject({ id: "session-1" });
    await expect(cancelCodexProfileDeviceAuth("account-a", "session-1")).resolves.toMatchObject({ id: "session-1" });
  });

  it("starts codex device auth without a profile", async () => {
    const fetchMock = stubFetchJson({
      id: "session-1",
      profile_id: "pending",
      status: "running",
      output: "ABCD-EFGH",
      error: null,
      created_at: "2026-07-09T00:02:00Z",
      completed_at: null,
    });

    await startCodexDeviceAuth();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/admin/codex-profiles/device-auth"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("parses the sanitized codex update status", async () => {
    const fetchMock = stubFetchJson({
      available: true,
      checked_at: "2026-07-12T01:02:03+00:00",
      version_before: "codex-cli 1.2.2",
      version_after: "codex-cli 1.2.3",
      succeeded: false,
      error: "update_failed_exit_7",
    });

    await expect(getCodexUpdateStatus()).resolves.toEqual({
      available: true,
      checked_at: "2026-07-12T01:02:03+00:00",
      version_before: "codex-cli 1.2.2",
      version_after: "codex-cli 1.2.3",
      succeeded: false,
      error: "update_failed_exit_7",
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/admin/codex-profiles/update-status", undefined);
  });

  it("parses an audit summary", async () => {
    stubFetchJson({
      total_users: 3,
      active_users: 2,
      job_counts: { queued: 1, succeeded: 2 },
      queue_length: 1,
      quota_policy: {
        window_hours: 5,
        window_limit: 8,
        parallel_generation_limit: 4,
        codex_usage_min_remaining_percent: 25,
        daily_generation_limit: 10,
        daily_generation_custom_limit_default: 20,
        daily_generation_reset_hour: 6,
      },
      total_calls: 3,
      success_rate: 0.667,
      failure_rate: 0,
      parallel_slots_used: 1,
      codex_quota: {
        available: false,
        checked_at: null,
        plan_type: null,
        source: "unavailable",
        error: "Codex usage status is unavailable",
        five_hour: null,
        weekly: null,
      },
      usage_trend: [],
      latest_job_updated_at: null,
    });

    await expect(getAuditSummary()).resolves.toMatchObject({
      total_users: 3,
      quota_policy: { window_hours: 5 },
      job_counts: { queued: 1 },
    });
  });

  it("updates quota policy with the backend payload shape", async () => {
    const fetchMock = stubFetchJson({
      window_hours: 12,
      window_limit: 20,
      parallel_generation_limit: 3,
      codex_usage_min_remaining_percent: 30,
      daily_generation_limit: 25,
      daily_generation_custom_limit_default: 20,
      daily_generation_reset_hour: 6,
    });

    await expect(
      updateQuotaPolicy({
        window_hours: 12,
        window_limit: 20,
        parallel_generation_limit: 3,
        codex_usage_min_remaining_percent: 30,
        daily_generation_limit: 25,
        daily_generation_custom_limit_default: 20,
        daily_generation_reset_hour: 6,
      }),
    ).resolves.toMatchObject({ parallel_generation_limit: 3 });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/audit/quota-policy",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          window_hours: 12,
          window_limit: 20,
          parallel_generation_limit: 3,
          codex_usage_min_remaining_percent: 30,
          daily_generation_limit: 25,
          daily_generation_custom_limit_default: 20,
          daily_generation_reset_hour: 6,
        }),
      }),
    );
  });

  it("saves and lists album items", async () => {
    const item = {
      id: "album-1",
      image_url: "/api/static/fixtures/kigurumi-candidate-1.webp",
      created_at: "2026-06-23T00:00:00Z",
      recipe: { face: { scale: 1.1 } },
      metadata: { candidate_index: 1 },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(item)))
      .mockResolvedValueOnce(new Response(JSON.stringify([item])));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      saveAlbumItem({
        image_url: item.image_url,
        recipe: item.recipe,
        metadata: item.metadata,
      }),
    ).resolves.toEqual(item);
    await expect(listAlbumItems()).resolves.toEqual([item]);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/album/items",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/album/items", undefined);
  });

  it("saves an edited album image as multipart data", async () => {
    const item = {
      id: "album-file-1",
      image_url: "/api/generated/album/album-file-1/edited.png",
      created_at: "2026-06-23T00:00:00Z",
      recipe: { face: { faceWidth: -2.4 } },
      metadata: { source: "editor" },
    };
    const fetchMock = stubFetchJson(item);
    const file = new File(["edited"], "edited.png", { type: "image/png" });

    await expect(
      saveAlbumImageFile({
        file,
        recipe: item.recipe,
        metadata: item.metadata,
      }),
    ).resolves.toEqual(item);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/album/items/file",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
      }),
    );
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const body = init.body as FormData;
    expect(body.get("file")).toBe(file);
    expect(body.get("recipe")).toBe(JSON.stringify(item.recipe));
    expect(body.get("metadata")).toBe(JSON.stringify(item.metadata));
  });
});

describe("reference API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uploads a reference file as multipart form data", async () => {
    const fetchMock = stubFetchJson({
      object_key: "references/upload-1/front.webp",
      file_name: "front.webp",
    });
    const file = new File(["reference"], "front.webp", { type: "image/webp" });

    await expect(uploadReferenceFile("front", file)).resolves.toEqual({
      objectKey: "references/upload-1/front.webp",
      fileName: "front.webp",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/references",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
      }),
    );

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const body = init.body as FormData;
    expect(body.get("kind")).toBe("front");
    expect(body.get("file")).toBe(file);
  });
});
