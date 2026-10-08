import { readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const forbidden = [
  /premium/i,
  /normal user/i,
  /normal_user/i,
  /premium_user/i,
  /advanced user/i,
  /advanced_user/i,
  /高级用户/,
  /普通用户/,
  /normal_quota/i,
  /premium_quota/i,
  /advanced_quota/i,
  /normal_base_quota/i,
  /premium_base_quota/i,
  /advanced_base_quota/i,
  /["']normal["']/i,
  /["']advanced["']/i,
];

const sourceRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

function collectPublicSourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return collectPublicSourceFiles(path);
    if (![".ts", ".tsx"].includes(extname(entry.name))) return [];
    if (entry.name.endsWith(".test.ts") || entry.name.endsWith(".test.tsx")) return [];
    if (entry.name === "vite-env.d.ts") return [];
    return [path];
  });
}

describe("public account copy", () => {
  it("matches bare tier string literals", () => {
    expect(forbidden.some((pattern) => pattern.test('const tier = "normal";'))).toBe(true);
    expect(forbidden.some((pattern) => pattern.test("const tier = 'advanced';"))).toBe(true);
  });

  it("does not flag normal prose", () => {
    const text = 'const prompt = "Allow normal anime references when the head is visible.";';

    expect(forbidden.some((pattern) => pattern.test(text))).toBe(false);
  });

  it("does not expose account tier wording in frontend source", () => {
    const files = collectPublicSourceFiles(sourceRoot);

    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
      const text = readFileSync(file, "utf-8");
      for (const pattern of forbidden) {
        expect(text, `${pattern} leaked in ${relative(sourceRoot, file)}`).not.toMatch(pattern);
      }
    }
  });
});
