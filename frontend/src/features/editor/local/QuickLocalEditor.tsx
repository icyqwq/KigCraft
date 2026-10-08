import Dialog from "@mui/material/Dialog";
import { useEffect, useRef, useState } from "react";
import { EditorWorkspace, type EditorImageSavePayload, type EditorWorkspaceHandle } from "../EditorWorkspace";
import type { EditorDraftContent } from "../drafts/types";
import { createEmptyRecipe } from "../deformation/recipe";
import { useEditorCopy } from "../useEditorCopy";
import { useMediaQuery } from "../../../ui/mui";
import { V2Dialog } from "../../../ui/V2Dialog";
import { WorkspaceHeader } from "../../design/WorkspaceHeader";
import { EditorBodySkeleton } from "../../design/Skeletons";
import { editorViewTools } from "../../design/editorViewTools";
import "../../design/editor-dialog.css";

const ACCEPTED = new Set(["image/png", "image/jpeg", "image/webp"]);
const MAX_BYTES = 64 * 1024 * 1024;

/** Reads the picked file's size; rejects what the editor cannot open. */
async function openImage(file: File) {
  if (!ACCEPTED.has(file.type) || !file.size || file.size > MAX_BYTES) throw new Error("Unsupported image");
  const bitmap = await createImageBitmap(file);
  const { width, height } = bitmap; bitmap.close();
  if (!width || !height || width > 16384 || height > 16384 || width * height > 67108864) throw new Error("Unsupported image");
  return { url: URL.createObjectURL(file), width, height };
}

/** What the user changed. Key points found or anchored by the editor itself do not count as an edit. */
function edits(draft: EditorDraftContent) {
  const { face, eyes, brows, mouth, liquify, annotations, details } = draft.recipe;
  return canonical({ face, eyes, brows, mouth, liquify, annotations, details, mask: draft.maskStrokes });
}

/** JSON with object keys sorted, so the same values always compare equal. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value && typeof value === "object") return "{" + Object.keys(value).sort()
    .map(key => JSON.stringify(key) + ":" + canonical((value as Record<string, unknown>)[key])).join(",") + "}";
  return JSON.stringify(value) ?? "null";
}

function downloadName(name: string) {
  const base = name.replace(/\.[^.]+$/, "").replace(/[\\/:*?"<>|]+/g, "-").trim() || "image";
  return base + "-kigcraft.png";
}

/**
 * Edits one image from this device in the same dialog as the conversation editor. Nothing is uploaded, stored or
 * kept as history: the result is only saved as a file on this device.
 */
export function QuickLocalEditor({ file, onClose }: { file: File; onClose: () => void }) {
  const { c } = useEditorCopy();
  const phone = useMediaQuery("(max-width: 767px)", false);
  const editor = useRef<EditorWorkspaceHandle>(null);
  const [image, setImage] = useState<{ url: string; width: number; height: number }>();
  const [failed, setFailed] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [saving, setSaving] = useState(false);
  // Drafts are compared with the untouched image, then with the last state saved to the device.
  const savedEdits = useRef(edits({ recipe: createEmptyRecipe(), maskStrokes: [] } as unknown as EditorDraftContent));
  const latestEdits = useRef(savedEdits.current);
  function changed(draft: EditorDraftContent) {
    latestEdits.current = edits(draft);
    setDirty(latestEdits.current !== savedEdits.current);
  }

  useEffect(() => {
    let url: string | undefined, cancelled = false;
    openImage(file).then(value => { url = value.url; if (cancelled) URL.revokeObjectURL(value.url); else setImage(value); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [file]);

  function close() { if (dirty) setConfirmClose(true); else onClose(); }
  async function save() {
    if (saving) return;
    setSaving(true);
    try { await editor.current?.save(); } finally { setSaving(false); }
  }
  function saved(payload: EditorImageSavePayload) {
    const link = document.createElement("a");
    const href = URL.createObjectURL(payload.imageBlob);
    link.href = href; link.download = downloadName(file.name);
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    savedEdits.current = latestEdits.current;
    setDirty(false);
  }

  return <Dialog fullScreen={phone} maxWidth={false} open transitionDuration={{ enter: 0, exit: 180 }}
    className="v2-workspace v2-workspace-editor v2-quick-local" onClose={close} aria-labelledby="quick-local-title">
    <div className="v2-editor">
      <WorkspaceHeader dialog titleId="quick-local-title" title={file.name || c("editorTitle")}
        backLabel={c("localClose")} onBack={close}
        primary={image ? <button type="button" className="v2-primary" disabled={saving} onClick={() => void save()}>{c("localSave")}</button> : undefined} />
      {failed ? <p role="alert">{c("localUnsupported")}</p> : null}
      {!image && !failed ? <EditorBodySkeleton /> : null}
      {image ? <div>
        <EditorWorkspace ref={editor} imageUrl={image.url} imageWidth={image.width} imageHeight={image.height}
          candidateIndex={1} imageCoordinateEditing downloadOnSave={false} saveLabel={c("localSave")}
          showRegenerateActions={false} availableTools={editorViewTools("front", false)} initialLiquifySymmetry
          onDraftChange={changed} onSave={saved} />
      </div> : null}
    </div>
    <V2Dialog open={confirmClose} size="sm" danger title={c("localDiscardTitle")} onClose={() => setConfirmClose(false)}
      actions={<><button type="button" onClick={() => setConfirmClose(false)}>{c("localKeep")}</button>
        <button type="button" className="v2-primary" onClick={onClose}>{c("localDiscard")}</button></>}>
      <p>{c("localDiscardBody")}</p>
    </V2Dialog>
  </Dialog>;
}
