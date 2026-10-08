/**
 * The working images of one edited picture, newest last.
 *
 * A local generation does not make a new version. Its result becomes the editor's working image (the "root" the
 * recipe is applied to); a version exists only once the user saves. This list remembers which result is in use, so closing
 * and reopening the editor resumes it, and lets the user step back to the image before the generation (and forward again).
 * It is kept per edited picture, so nothing here touches any other picture.
 */
export type EditorRootsTarget = { ownerId: string; branchId: string; revisionId: string; baseRootAssetId: string };
type Saved = { schema: 1; base: string; entries: string[]; position: number };

export class EditorRoots {
  readonly key: string;
  private state: Saved;
  constructor(private target: EditorRootsTarget, private storage: Storage = localStorage) {
    this.key = "kig-v2-editor-roots:" + JSON.stringify([target.ownerId, target.branchId, target.revisionId]);
    this.state = this.read();
  }
  private fresh(): Saved { return { schema: 1, base: this.target.baseRootAssetId, entries: [], position: -1 }; }
  private read(): Saved {
    try {
      const value = JSON.parse(this.storage.getItem(this.key) ?? "null");
      if (value && value.schema === 1 && value.base === this.target.baseRootAssetId && Array.isArray(value.entries) &&
          value.entries.every((item: unknown) => typeof item === "string") &&
          Number.isInteger(value.position) && value.position >= -1 && value.position < value.entries.length) return value;
    } catch { /* unreadable record: start from the picture's own image */ }
    return this.fresh();
  }
  private save() {
    try {
      if (this.state.entries.length) this.storage.setItem(this.key, JSON.stringify(this.state));
      else this.storage.removeItem(this.key);
    } catch { /* the edit itself still works; only resuming after a reload is lost */ }
  }
  /** The asset the editor should currently work on. */
  current(): string { return this.state.position < 0 ? this.target.baseRootAssetId : this.state.entries[this.state.position]; }
  /** True when the editor works on a local-generation result instead of the picture's own image. */
  adopted(): boolean { return this.state.position >= 0; }
  canUndo(): boolean { return this.state.position >= 0; }
  canRedo(): boolean { return this.state.position < this.state.entries.length - 1; }
  adopt(assetId: string) {
    if (this.current() === assetId) return;
    this.state = { ...this.state, entries: [...this.state.entries.slice(0, this.state.position + 1), assetId] };
    this.state.position = this.state.entries.length - 1;
    this.save();
  }
  undo() { if (this.canUndo()) { this.state.position -= 1; this.save(); } }
  redo() { if (this.canRedo()) { this.state.position += 1; this.save(); } }
  /** After a save the new version has its own root. */
  reset() { this.state = this.fresh(); this.save(); }
}
