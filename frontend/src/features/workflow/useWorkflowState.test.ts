import { describe, expect, it } from "vitest";

import {
  buildInitialWorkflowState,
  restoreWorkflowState,
  serializeWorkflowState,
} from "./useWorkflowState";

describe("workflow state persistence", () => {
  it("returns initial state when no persisted state exists", () => {
    expect(restoreWorkflowState(null)).toEqual(buildInitialWorkflowState());
    expect(buildInitialWorkflowState().detailConfirmation).toEqual({
      analysisId: null,
      features: [],
      crops: [],
      warnings: [],
    });
  });

  it("returns initial state for malformed JSON", () => {
    expect(restoreWorkflowState("{not-json")).toEqual(buildInitialWorkflowState());
  });

  it("returns initial state for schema-invalid JSON", () => {
    expect(restoreWorkflowState(JSON.stringify({ step: 4 }))).toEqual(buildInitialWorkflowState());
  });

  it("restores active session and job ids", () => {
    const state = buildInitialWorkflowState();
    state.characterSessionId = "session-a";
    state.activeJobId = "job-a";
    state.step = 2;

    const restored = restoreWorkflowState(serializeWorkflowState(state));

    expect(restored.characterSessionId).toBe("session-a");
    expect(restored.activeJobId).toBe("job-a");
    expect(restored.step).toBe(2);
  });

  it("serializes and restores persisted reference slots without transient file data", () => {
    const state = buildInitialWorkflowState();
    state.referenceSlots = [
      {
        kind: "front",
        label: "Front",
        required: true,
        description: "Main front reference",
        fileName: "front.webp",
        objectKey: "references/upload/front.webp",
        previewUrl: "blob:front-preview",
        file: new File(["front"], "front.webp", { type: "image/webp" }),
      },
    ];

    const serialized = JSON.parse(serializeWorkflowState(state));
    const restored = restoreWorkflowState(JSON.stringify(serialized));

    expect(serialized.referenceSlots).toEqual([
      {
        kind: "front",
        label: "Front",
        required: true,
        description: "Main front reference",
        fileName: "front.webp",
        objectKey: "references/upload/front.webp",
      },
    ]);
    expect(restored.referenceSlots).toEqual(serialized.referenceSlots);
  });

  it("serializes callers without detail confirmation using the default detail state", () => {
    const { detailConfirmation: _detailConfirmation, ...legacyState } = buildInitialWorkflowState();

    expect(JSON.parse(serializeWorkflowState(legacyState))).toMatchObject({
      detailConfirmation: {
        analysisId: null,
        features: [],
        crops: [],
        warnings: [],
      },
    });
  });

  it("restores detail confirmation state", () => {
    const state = buildInitialWorkflowState();
    state.step = 4;
    state.detailConfirmation = {
      analysisId: "analysis-a",
      features: [
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
      warnings: ["Low resolution"],
    };

    const restored = restoreWorkflowState(serializeWorkflowState(state));

    expect(restored.step).toBe(4);
    expect(restored.detailConfirmation).toEqual(state.detailConfirmation);
  });

  it("restores persisted ears detail confirmation state", () => {
    const state = buildInitialWorkflowState();
    state.step = 2;
    state.detailConfirmation = {
      analysisId: "analysis-ears",
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
          sourceReferenceKey: "front:references/upload-a/front.webp",
          bbox: { x: 0.25, y: 0.05, width: 0.4, height: 0.25 },
          objectKey: "references/analysis-ears/detail-ears.webp",
          imageUrl: "/api/references/references/analysis-ears/detail-ears.webp",
        },
      ],
      warnings: [],
    };

    const restored = restoreWorkflowState(serializeWorkflowState(state));

    expect(restored.step).toBe(2);
    expect(restored.detailConfirmation).toEqual(state.detailConfirmation);
  });
});
