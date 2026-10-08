import { forwardRef, Suspense, useEffect, useImperativeHandle, useRef, useState, type ComponentType, type CSSProperties } from "react";
import {ErrorToast} from "../../ui/ErrorToast";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Popover from "@mui/material/Popover";
import { IconCheck, IconPlus, IconX } from "@tabler/icons-react";
import { V2Dialog } from "../../ui/V2Dialog";
import { ReferenceSelectionError, appendReferenceFiles, type ReferenceAttachment, type ReferenceOption } from "./attachments";
import { Preview, type ReferenceAttachmentsHandle, type ReferenceLibraryProps } from "./ReferenceAttachments";
import { useHasLegacyReferences } from "./legacyAvailability";
import { useReferenceCopy } from "./useReferenceCopy";
import "./references.css";

type Props = {
  items: ReferenceAttachment[]; onChange: (items: ReferenceAttachment[]) => void; maximum: number; disabled?: boolean;
  /** Tiles for "choose from this design": earlier references and generated versions. */
  options?: ReferenceOption[]; upload?: (file: File) => Promise<{ id: string }>;
  library?: ComponentType<ReferenceLibraryProps>; legacyOwnerId?: string; label?: string; fileInputTestId?: string;
  /** Only a fresh upload: the (+) opens the file picker directly, with no design or legacy sources. */
  uploadOnly?: boolean;
};
const accept = "image/png,image/jpeg,image/webp";

/**
 * Composer variant of the reference attachments: one (+) entry, a 56 px thumbnail strip with a purpose bubble,
 * and a thumbnail picker for images that already belong to the design. The editor keeps the original list.
 */
export const ReferenceAttachmentsCompact = forwardRef<ReferenceAttachmentsHandle, Props>(function ReferenceAttachmentsCompact(
  { items, onChange, maximum, disabled = false, options = [], upload, library: LegacyMaterials, legacyOwnerId, label, fileInputTestId, uploadOnly = false }, ref) {
  const c = useReferenceCopy();
  const input = useRef<HTMLInputElement>(null), replace = useRef<HTMLInputElement>(null);
  const [replaceKey, setReplaceKey] = useState<string>();
  const [menu, setMenu] = useState<HTMLElement | null>(null);
  const [bubble, setBubble] = useState<{ key: string; anchor: HTMLElement } | null>(null);
  const [picker, setPicker] = useState(false), [chosen, setChosen] = useState<string[]>([]);
  const [legacyOpen, setLegacyOpen] = useState(false);
  const hasLegacy = useHasLegacyReferences(legacyOwnerId && LegacyMaterials ? legacyOwnerId : undefined);
  const [error, setError] = useState<ReferenceSelectionError | "unknown" | null>(null);
  const [failed, setFailed] = useState<Record<string, File>>({});
  const [uploading, setUploading] = useState<string[]>([]);
  const inflight = useRef(new Set<string>()), live = useRef(true), latest = useRef(items); latest.current = items;
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => setLegacyOpen(false), [legacyOwnerId]);
  const selectionError = (failure: unknown) => setError(failure instanceof ReferenceSelectionError ? failure : "unknown");
  const errorMessage = error instanceof ReferenceSelectionError
    ? c(error.reason, { maximum: error.maximum, files: error.files.join("、") })
    : error ? c("selectionFailed") : items.length > maximum ? c("overLimit") : "";
  async function retry(item: ReferenceAttachment) {
    if (!upload || !item.file || disabled || inflight.current.has(item.key)) return;
    inflight.current.add(item.key); setUploading([...inflight.current]);
    try {
      const asset = await upload(item.file);
      if (live.current) onChange(latest.current.map(old => old.key === item.key && old.file === item.file ? { ...old, assetId: asset.id } : old));
    } catch { if (live.current) setFailed(current => ({ ...current, [item.key]: item.file! })); }
    finally { inflight.current.delete(item.key); if (live.current) setUploading([...inflight.current]); }
  }
  function addFiles(files: Iterable<File>) {
    if (disabled) return;
    const selected = Array.from(files); if (!selected.length) return;
    try { onChange(appendReferenceFiles(items, selected, maximum)); setError(null); }
    catch (failure) { selectionError(failure); }
  }
  useImperativeHandle(ref, () => ({ addFiles, markUploadFailure: (key, file) => setFailed(current => ({ ...current, [key]: file })) }),
    [items, maximum, disabled, onChange]);
  function toggle(option: ReferenceOption) {
    if (disabled) return;
    if (items.some(item => item.assetId === option.key)) { onChange(items.filter(item => item.assetId !== option.key)); setError(null); return; }
    if (items.length >= maximum) { setError(new ReferenceSelectionError("limit", maximum)); return; }
    onChange([...items, { key: crypto.randomUUID(), assetId: option.key, usage: option.description ?? "" }]); setError(null);
  }
  const remaining = Math.max(0, maximum - items.length);
  function openPicker() { setMenu(null); setChosen([]); setPicker(true); }
  function confirmPicker() {
    const picked = options.filter(option => chosen.includes(option.key) && !items.some(item => item.assetId === option.key)).slice(0, remaining);
    if (picked.length) onChange([...items, ...picked.map(option => ({ key: crypto.randomUUID(), assetId: option.key, usage: option.kind === "reference" ? option.description ?? "" : "" }))]);
    setError(null); setPicker(false);
  }
  const optionLabel = (option: ReferenceOption) => option.kind === "version" && option.versionNumber !== undefined
    ? c("versionTag", { number: option.versionNumber })
    : option.referenceNumber !== undefined ? c("number", { number: option.referenceNumber }) : option.label;
  const bubbleIndex = bubble ? items.findIndex(item => item.key === bubble.key) : -1;
  const bubbleItem = bubbleIndex >= 0 ? items[bubbleIndex] : undefined;
  const failures = items.flatMap((item, index) => failed[item.key] === item.file && item.file && !item.assetId ? [{ item, index }] : []);
  return <section className="reference-attachments is-compact" aria-label={label ?? c("references")}
    onDragOver={event => { event.preventDefault(); event.stopPropagation(); }}
    onDrop={event => { event.preventDefault(); event.stopPropagation(); addFiles(event.dataTransfer.files); }}
    onPaste={event => { if (event.clipboardData.files.length) { event.preventDefault(); event.stopPropagation(); addFiles(event.clipboardData.files); } }}>
    {items.length ? <ul className="reference-strip">{items.map((item, index) => <li key={item.key} className="reference-thumb">
      <button type="button" className="reference-thumb-open" aria-label={c("usage", { number: index + 1 })} aria-haspopup="dialog"
        aria-expanded={bubble?.key === item.key} onClick={event => setBubble({ key: item.key, anchor: event.currentTarget })}>
        <Preview item={item} option={options.find(option => option.key === item.assetId)} index={index} />
        <span className="reference-thumb-number" aria-hidden="true">{index + 1}</span>
        {!item.usage.trim() ? <span className="reference-thumb-dot" title={c("usageMissing")} aria-hidden="true" /> : null}
      </button>
      <button type="button" className="reference-thumb-remove" disabled={disabled} aria-label={c("removeNumber", { number: index + 1 })}
        onClick={() => onChange(items.filter(old => old.key !== item.key))}><IconX size={16} stroke={1.75} /></button>
    </li>)}</ul> : null}
    <input ref={input} data-testid={fileInputTestId} type="file" multiple hidden accept={accept}
      onChange={event => { addFiles(event.target.files ?? []); event.target.value = ""; }} />
    <input ref={replace} type="file" hidden aria-label={c("replaceFile")} accept={accept}
      onChange={event => {
        const file = event.target.files?.[0]; event.target.value = ""; if (!file || !replaceKey || disabled) return;
        try {
          const valid = appendReferenceFiles([], [file], 1)[0];
          onChange(items.map(old => old.key === replaceKey ? { key: old.key, usage: old.usage, file: valid.file } : old)); setError(null);
        } catch (failure) { selectionError(failure); }
      }} />
    <button type="button" className="v2-quiet v2-icon reference-add" disabled={disabled || remaining === 0} aria-label={c("add")} title={c("add")}
      aria-haspopup={uploadOnly ? undefined : "menu"} aria-expanded={uploadOnly ? undefined : Boolean(menu)}
      onClick={event => uploadOnly ? input.current?.click() : setMenu(event.currentTarget)}><IconPlus size={18} stroke={1.75} /></button>
    <Menu anchorEl={menu} open={!uploadOnly && Boolean(menu)} onClose={() => setMenu(null)}>
      <MenuItem onClick={() => { setMenu(null); input.current?.click(); }}>{c("uploadImage")}</MenuItem>
      <MenuItem disabled={!options.length} onClick={openPicker}>{c("chooseFromDesign")}</MenuItem>
      {legacyOwnerId && LegacyMaterials && hasLegacy ? <MenuItem onClick={() => { setMenu(null); setLegacyOpen(true); }}>{c("legacyMenu")}</MenuItem> : null}
    </Menu>
    {failures.map(({ item, index }) => <ErrorToast key={item.key} message={c("uploadFailed")}
      action={upload ? { label: c("retryNumber", { number: index + 1 }), onClick: () => void retry(item) } : undefined} />)}
    <ErrorToast message={errorMessage || null} />
    <Popover open={Boolean(bubble && bubbleItem)} anchorEl={bubble?.anchor} onClose={() => setBubble(null)}
      anchorOrigin={{ vertical: "top", horizontal: "left" }} transformOrigin={{ vertical: "bottom", horizontal: "left" }}>
      {bubbleItem ? <div className="reference-popover">
        <div className="reference-popover-preview"><Preview item={bubbleItem} option={options.find(option => option.key === bubbleItem.assetId)} index={bubbleIndex} /></div>
        <input autoFocus aria-label={c("usage", { number: bubbleIndex + 1 })} placeholder={c("usagePlaceholder")} value={bubbleItem.usage} maxLength={2000}
          disabled={disabled} onChange={event => onChange(items.map(old => old.key === bubbleItem.key ? { ...old, usage: event.target.value } : old))}
          onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); setBubble(null); } }} />
        {bubbleItem.file ? <span className="reference-file-name">{bubbleItem.file.name}</span> : null}
        <div className="reference-popover-actions">
          {bubbleItem.file ? <button type="button" disabled={disabled} aria-label={c("replaceNumber", { number: bubbleIndex + 1 })}
            onClick={() => { setReplaceKey(bubbleItem.key); replace.current?.click(); }}>{c("replace")}</button> : null}
          <button type="button" className="v2-primary" onClick={() => setBubble(null)}>{c("closeUsage")}</button>
        </div>
      </div> : null}
    </Popover>
    <V2Dialog open={picker} size="md" title={c("chooseFromDesign")} onClose={() => setPicker(false)}
      actions={<><button type="button" onClick={() => setPicker(false)}>{c("cancel")}</button>
        <button type="button" className="v2-primary" disabled={!chosen.length} onClick={confirmPicker}>{c("pickerAdd", { count: chosen.length })}</button></>}>
      {options.length ? <>
        {remaining < options.length ? <p className="v2-status">{c("pickerLimit", { count: remaining })}</p> : null}
        <ul className="reference-picker-grid">{options.map((option, index) => {
          const added = items.some(item => item.assetId === option.key), picked = chosen.includes(option.key);
          return <li key={option.key} style={{ animationDelay: Math.min(index, 5) * 30 + "ms" } as CSSProperties}>
            <button type="button" aria-pressed={added || picked} aria-label={optionLabel(option)} title={added ? c("added") : undefined}
              disabled={added || (!picked && chosen.length >= remaining)}
              onClick={() => setChosen(current => picked ? current.filter(key => key !== option.key) : [...current, option.key])}>
              <img src={option.imageUrl ?? "/api/v2/assets/" + encodeURIComponent(option.key)} alt="" loading="lazy" />
              <span className="reference-picker-label v2-version-stamp">{optionLabel(option)}</span>
              {added || picked ? <span className="reference-picker-check" aria-hidden="true"><IconCheck size={16} stroke={1.75} /></span> : null}
            </button></li>;
        })}</ul></> : <p>{c("pickerEmpty")}</p>}
    </V2Dialog>
    {legacyOwnerId && LegacyMaterials ? <V2Dialog open={legacyOpen} size="md" className="legacy-materials-dialog" labelId="legacy-reference-title" title={c("legacyChoose")}
      onClose={() => setLegacyOpen(false)} actions={<button type="button" className="v2-primary" onClick={() => setLegacyOpen(false)}>{c("legacyDone")}</button>}>
      {legacyOpen ? <Suspense fallback={<p role="status">{c("legacyLoading")}</p>}>
        <LegacyMaterials key={legacyOwnerId} ownerId={legacyOwnerId} onChoose={toggle}
          selected={items.flatMap(item => item.assetId ? [item.assetId] : [])}
          maximum={maximum - items.filter(item => !item.assetId).length} disabled={disabled} />
      </Suspense> : null}
    </V2Dialog> : null}
  </section>;
});
