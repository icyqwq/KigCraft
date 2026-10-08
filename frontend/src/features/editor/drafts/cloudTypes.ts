import type { EditorDraftContent } from "./types";

export type CloudReference = { key:string; assetId:string; usage:string; fileName?:string; lastModified?:number };
export type CloudContent = Omit<EditorDraftContent, "localUploadedReferenceFile" | "extraReferenceFile" | "localReferences"> & {localReferences?:CloudReference[]};
export type CloudAttachment = { slot: "local" | "extra"; asset_id: string; file_name: string; last_modified: number };
export type CloudDocument = { schema_version: 1 | 2; source_sha256: string; width: number; height: number; content: CloudContent; attachments: CloudAttachment[] };
export type CloudRequest = { client_request_id: string; expected_version: number; device_id: string;
  branch_id: string; base_revision_id: string; root_asset_id: string; document: CloudDocument };
export type CloudState = { id: string; version: number; syncedLocalVersion?: number; syncedContentDigest?: string;
  pending?: { request: CloudRequest; localVersion: number; contentDigest?: string };
  pendingDelete?: { requestId: string; version: number; contentDigest: string; acknowledged?: boolean }; uploads: Record<string, string>; conflictCopy?: boolean };
export type CloudRecord = { id: string; version: number; branch_id: string; base_revision_id: string;
  root_asset_id: string; document: CloudDocument; device_id: string; updated_at: string; root_image_url: string };
