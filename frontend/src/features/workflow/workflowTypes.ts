import type { DetailCrop, DetailFeature } from "../../api/client";

export type WorkflowStep = 1 | 2 | 3 | 4;

export type ReferenceKind = "front" | "side" | "back" | "expression" | "accessory" | "annotation" | "supplemental";

export type ReferenceSlot = {
  kind: ReferenceKind;
  description?: string;
  label: string;
  required: boolean;
  fileName?: string;
  objectKey?: string;
  previewUrl?: string;
  file?: File;
};

export type DetailConfirmationState = {
  analysisId: string | null;
  features: DetailFeature[];
  crops: DetailCrop[];
  warnings: string[];
};

export type WorkflowState = {
  step: WorkflowStep;
  characterSessionId: string | null;
  activeJobId: string | null;
  selectedCandidateIndex: number | null;
  selectedRequirementIds: string[];
  freeText: string;
  referenceSlots: ReferenceSlot[];
  detailConfirmation: DetailConfirmationState;
};
