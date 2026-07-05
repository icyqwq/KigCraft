import { afterEach, describe, expect, it, vi } from "vitest";

import {
  acceptGenerationCandidate,
  analyzeReferenceDetails,
  createGenerationJob,
  createLocalRevisionJob,
  getAuditSummary,
  getGenerationEvents,
  getGenerationJob,
  listAlbumItems,
  saveAlbumImageFile,
  saveAlbumItem,
  uploadReferenceFile,
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

describe("audit and album API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses an audit summary", async () => {
    stubFetchJson({
      total_users: 3,
      active_users: 2,
      job_counts: { queued: 1, succeeded: 2 },
      queue_length: 1,
      quota_policy: {
        window_hours: 5,
        normal_window_limit: 8,
        premium_unlimited: true,
        parallel_generation_limit: 8,
      },
      total_calls: 3,
      success_rate: 0.667,
      failure_rate: 0,
      parallel_slots_used: 1,
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
      normal_window_limit: 20,
      premium_unlimited: false,
      parallel_generation_limit: 3,
    });

    await expect(
      updateQuotaPolicy({
        window_hours: 12,
        normal_window_limit: 20,
        premium_unlimited: false,
        parallel_generation_limit: 3,
      }),
    ).resolves.toMatchObject({ parallel_generation_limit: 3 });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/audit/quota-policy",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          window_hours: 12,
          normal_window_limit: 20,
          premium_unlimited: false,
          parallel_generation_limit: 3,
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
