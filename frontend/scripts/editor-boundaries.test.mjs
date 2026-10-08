import { strict as assert } from "node:assert";
import { test } from "node:test";
import path from "node:path";
import { auditSources } from "./editor-boundaries.mjs";

function audit(files) {
  const sources = new Map(Object.entries(files));
  return auditSources(sources, (specifier, from) => {
    const target = path.posix.normalize(path.posix.join(path.posix.dirname(from), specifier));
    return [target, target + ".ts", target + ".tsx"].find(name => sources.has(name));
  });
}
test("follows type imports, re-exports and lazy imports through intermediate modules", () => {
  for (const statement of [
    'import type { Value } from "./bridge";',
    'export { value } from "./bridge";',
    'const pending = import("./bridge");',
    'type Value = import("./bridge").Value;',
  ]) {
    const result = audit({
      "features/editor/core/test.ts": statement,
      "features/editor/core/bridge.ts": 'export { value } from "../../design/api";',
      "features/design/api.ts": 'export const value = 1;',
    });
    assert.ok(result.errors.some(error => error.includes("editor business dependency")));
  }
});
test("accepts injected business callbacks without traversing the caller", () => {
  const result = audit({
    "features/editor/core/test.ts": 'export type Save = (value: unknown) => Promise<void>;',
    "features/design/api.ts": 'import type { Save } from "../editor/core/test"; export const save: Save = async () => {};',
  });
  assert.deepEqual(result.errors, []);
  assert.equal(result.editorReachableFiles, 1);
});
test("detects new any and new suppression but ignores string text and method names", () => {
  assert.equal(audit({"example.ts": 'const x: any = 1; // @ts-ignore\nx();'}).errors.length, 2);
  assert.deepEqual(audit({"example.ts": 'const x = "any @ts-ignore"; AbortSignal.any([]);'}).errors, []);
  assert.ok(audit({"ui/mui.tsx": Array(3).fill("const c = MuiStack as any;").join("\n")}).errors.length);
});
test("rejects network access and computed imports from editor code", () => {
  for (const text of ['fetch("/api");', 'window.fetch("/api");', 'new EventSource("/api");', 'import(source);']) {
    assert.ok(audit({"features/editor/core/test.ts": text}).errors.length);
  }
});
