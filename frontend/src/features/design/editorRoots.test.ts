import { beforeEach, describe, expect, it } from "vitest";
import { EditorRoots } from "./editorRoots";

const target = { ownerId: "u", branchId: "b", revisionId: "r1", baseRootAssetId: "root" };
beforeEach(() => localStorage.clear());

describe("EditorRoots", () => {
  it("starts at the picture's own image", () => {
    const roots = new EditorRoots(target);
    expect(roots.current()).toBe("root");
    expect([roots.adopted(), roots.canUndo(), roots.canRedo()]).toEqual([false, false, false]);
  });

  it("adopts a result, steps back and forward, and drops the redo branch on a new result", () => {
    const roots = new EditorRoots(target);
    roots.adopt("a");
    roots.adopt("b");
    expect(roots.current()).toBe("b");
    roots.undo();
    expect([roots.current(), roots.canRedo()]).toEqual(["a", true]);
    roots.undo();
    expect([roots.current(), roots.adopted()]).toEqual(["root", false]);
    roots.redo();
    roots.adopt("c");
    expect(roots.current()).toBe("c");
    expect(roots.canRedo()).toBe(false);
    roots.undo();
    expect(roots.current()).toBe("a");
  });

  it("resumes after the editor is closed and reopened, per picture", () => {
    new EditorRoots(target).adopt("a");
    expect(new EditorRoots(target).current()).toBe("a");
    expect(new EditorRoots({ ...target, revisionId: "r2" }).current()).toBe("root");
    expect(new EditorRoots({ ...target, ownerId: "other" }).current()).toBe("root");
  });

  it("ignores a record made for a different root and forgets everything after a save", () => {
    new EditorRoots(target).adopt("a");
    expect(new EditorRoots({ ...target, baseRootAssetId: "changed" }).current()).toBe("changed");
    const roots = new EditorRoots(target);
    roots.reset();
    expect(new EditorRoots(target).current()).toBe("root");
    expect(localStorage.length).toBe(0);
  });

  it("survives unreadable storage", () => {
    const key = new EditorRoots(target).key;
    localStorage.setItem(key, "{not json");
    expect(new EditorRoots(target).current()).toBe("root");
  });
});
