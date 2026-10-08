import type { MaskBinding } from "./maskBinding";
import type { EditRecipe } from "../deformation/recipe";
import type { LocalMaskStroke } from "../localGeneration";

export type EditorHistoryState = { recipe: EditRecipe; maskStrokes: LocalMaskStroke[]; maskBinding?: MaskBinding };
type Entry = { before: EditorHistoryState; after: EditorHistoryState };
const copy = <T>(value: T): T => structuredClone(value);
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** One instance per editor. Viewport and temporary tool preferences never enter this history. */
export class EditorHistory {
  private current: EditorHistoryState;
  private revision = 0;
  get version() { return this.revision; }
  private past: Entry[] = [];
  private future: Entry[] = [];
  private gesture: { key: string; before: EditorHistoryState } | null = null;
  constructor(initial: EditorHistoryState, private readonly capacity = 100) { this.current = copy(initial); }
  get snapshot() { return copy(this.current); }
  get canUndo() { return this.past.length > 0 || Boolean(this.gesture && !same(this.gesture.before, this.current)); }
  get canRedo() { return this.future.length > 0 && !(this.gesture && !same(this.gesture.before, this.current)); }
  get gestureKey() { return this.gesture?.key; }
  begin(key: string) {
    if (this.gesture?.key === key) return;
    this.commit();
    this.gesture = { key, before: copy(this.current) };
  }
  apply(next: EditorHistoryState, key?: string) {
    if (key) this.begin(key);
    else this.commit();
    if (same(next, this.current)) return;
    const before = this.current;
    this.current = copy(next); ++this.revision;
    if (!this.gesture) this.push({ before, after: this.current });
  }
  commit() {
    const gesture = this.gesture;
    this.gesture = null;
    if (gesture && !same(gesture.before, this.current)) this.push({ before: gesture.before, after: this.current });
  }
  cancel() {
    if (this.gesture && !same(this.current, this.gesture.before)) { this.current = this.gesture.before; ++this.revision; }
    this.gesture = null;
    return this.snapshot;
  }
  undo() {
    this.commit();
    const entry = this.past.pop();
    if (entry) { this.future.push(entry); this.current = copy(entry.before); ++this.revision; }
    return this.snapshot;
  }
  redo() {
    this.commit();
    const entry = this.future.pop();
    if (entry) { this.past.push(entry); this.current = copy(entry.after); ++this.revision; }
    return this.snapshot;
  }
  reset(state: EditorHistoryState) {
    this.current = copy(state); ++this.revision; this.past = []; this.future = []; this.gesture = null;
  }
  /** Automatic initialization is a baseline correction, not an undoable user action. */
  rebaseRecipe(next: EditRecipe) {
    const keys = (Object.keys(next) as Array<keyof EditRecipe>)
      .filter(key => !same(this.current.recipe[key], next[key]));
    if (keys.length) ++this.revision;
    const rebase = (state: EditorHistoryState): EditorHistoryState => ({
      ...state, recipe: Object.assign({}, state.recipe, Object.fromEntries(keys.map(key => [key, copy(next[key])]))),
    });
    this.current = rebase(this.current);
    this.past = this.past.map(entry => ({ before: rebase(entry.before), after: rebase(entry.after) }));
    this.future = this.future.map(entry => ({ before: rebase(entry.before), after: rebase(entry.after) }));
    if (this.gesture) this.gesture.before = rebase(this.gesture.before);
  }
  private push(entry: Entry) {
    this.past.push(copy(entry));
    this.past = this.past.slice(-this.capacity);
    this.future = [];
  }
}
