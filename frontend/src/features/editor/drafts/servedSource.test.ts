import { afterEach, expect, it, vi } from "vitest";
import { inspectServedSource } from "./repository";

afterEach(() => vi.unstubAllGlobals());

it("identifies a watermarked server image by the stored original it stands for", async () => {
  vi.stubGlobal("createImageBitmap", async () => ({ width: 4, height: 3, close() {} }));
  const original = "a".repeat(64);
  const served = await inspectServedSource(new Response(new Blob([new Uint8Array([1, 2, 3])]), { headers: { "X-Source-Sha256": original } }));
  expect(served).toMatchObject({ sha256: original, width: 4, height: 3 });
  // Without (or with a malformed) server identity the bytes themselves are hashed.
  const plain = await inspectServedSource(new Response(new Blob([new Uint8Array([1, 2, 3])]), { headers: { "X-Source-Sha256": "nope" } }));
  expect(plain.sha256).toMatch(/^[0-9a-f]{64}$/);
  expect(plain.sha256).not.toBe(original);
});
