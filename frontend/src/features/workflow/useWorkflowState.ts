import { z } from "zod";

import type { ReferenceSlot, WorkflowState } from "./workflowTypes";

const detailKindSchema = z.enum([
  "hair",
  "eyes",
  "expression",
  "headwear",
  "accessory",
  "ears",
  "outfit",
  "color",
  "avoid",
  "other",
]);

const detailFeatureSchema = z.object({
  id: z.string(),
  kind: detailKindSchema,
  label: z.string().default(""),
  description: z.string(),
  confidence: z.number().nullable().optional(),
});

const detailCropSchema = z.object({
  id: z.string(),
  kind: detailKindSchema,
  description: z.string(),
  sourceReferenceKey: z.string(),
  bbox: z.object({
    x: z.number(),
    y: z.number(),
    width: z.number(),
    height: z.number(),
  }),
  objectKey: z.string(),
  imageUrl: z.string(),
});

const referenceKindSchema = z.enum([
  "front",
  "side",
  "back",
  "expression",
  "accessory",
  "annotation",
  "supplemental",
]);

const referenceSlotSchema = z.object({
  kind: referenceKindSchema,
  description: z.string().optional(),
  label: z.string(),
  required: z.boolean(),
  fileName: z.string().optional(),
  objectKey: z.string().optional(),
});

function buildInitialDetailConfirmationState() {
  return {
    analysisId: null,
    features: [],
    crops: [],
    warnings: [],
  };
}

const detailConfirmationSchema = z.object({
  analysisId: z.string().nullable(),
  features: z.array(detailFeatureSchema),
  crops: z.array(detailCropSchema),
  warnings: z.array(z.string()),
});

type SerializableWorkflowState = Omit<WorkflowState, "detailConfirmation" | "referenceSlots"> & {
  detailConfirmation?: WorkflowState["detailConfirmation"];
  referenceSlots?: ReferenceSlot[];
};

const workflowStateSchema = z.object({
  step: z.union([z.literal(1), z.literal(2), z.literal(3), z.literal(4)]),
  characterSessionId: z.string().nullable(),
  activeJobId: z.string().nullable(),
  selectedCandidateIndex: z.number().nullable(),
  selectedRequirementIds: z.array(z.string()),
  freeText: z.string(),
  referenceSlots: z.array(referenceSlotSchema).default([]),
  detailConfirmation: detailConfirmationSchema.default(() => buildInitialDetailConfirmationState()),
});

function sanitizeReferenceSlots(referenceSlots: ReferenceSlot[] = []) {
  return referenceSlots.map(({ description, fileName, kind, label, objectKey, required }) => ({
    ...(description ? { description } : {}),
    ...(fileName ? { fileName } : {}),
    kind,
    label,
    ...(objectKey ? { objectKey } : {}),
    required,
  }));
}

export function buildInitialWorkflowState(): WorkflowState {
  return {
    step: 1,
    characterSessionId: null,
    activeJobId: null,
    selectedCandidateIndex: null,
    selectedRequirementIds: [],
    freeText: "",
    referenceSlots: [],
    detailConfirmation: buildInitialDetailConfirmationState(),
  };
}

export function serializeWorkflowState(state: SerializableWorkflowState): string {
  return JSON.stringify({
    ...state,
    referenceSlots: sanitizeReferenceSlots(state.referenceSlots),
    detailConfirmation: state.detailConfirmation ?? buildInitialDetailConfirmationState(),
  });
}

export function restoreWorkflowState(raw: string | null): WorkflowState {
  if (!raw) return buildInitialWorkflowState();

  try {
    const parsed = workflowStateSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : buildInitialWorkflowState();
  } catch {
    return buildInitialWorkflowState();
  }
}
