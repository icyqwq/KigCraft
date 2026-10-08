import { editorDrafts } from "./repository";
import type { DraftScope, DraftSource, EditorDraftContent, EditorDraftRecord } from "./types";

/** Each mounted editor owns a queue; old completions cannot mark newer content as saved. */
export class DraftSession {
  private tail: Promise<void> = Promise.resolve();
  private generation = 0;
  private persistedGeneration = 0;
  private lastError: unknown;
  record?: EditorDraftRecord;
  constructor(private scope: DraftScope, private source: DraftSource, record?: EditorDraftRecord) { this.record = record; }
  get dirty() { return this.generation !== this.persistedGeneration; }
  save(content: EditorDraftContent): Promise<EditorDraftRecord> {
    const generation = ++this.generation;
    const snapshot = structuredClone(content);
    const work = this.tail.then(async () => {
      const record = await editorDrafts.save(this.scope, this.source, snapshot, this.record);
      this.record = record; this.persistedGeneration = generation; this.lastError = undefined;
      return record;
    });
    this.tail = work.then(() => undefined, error => { this.lastError = error; });
    return work;
  }
  async flush() { await this.tail; if (this.lastError) throw this.lastError; }
  async discard(expected: Pick<EditorDraftRecord, "id" | "version">) {
    await this.flush();
    if (this.record?.id !== expected.id || this.record.version !== expected.version) return false;
    return editorDrafts.remove(this.scope, expected);
  }
}
