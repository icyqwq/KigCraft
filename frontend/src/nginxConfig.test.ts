import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("nginx production config", () => {
  it("allows local revision multipart image payloads larger than the nginx 1MB default", () => {
    const config = readFileSync(resolve(process.cwd(), "nginx.conf"), "utf8");
    const match = config.match(/\bclient_max_body_size\s+(\d+)([mMgG])\s*;/);

    expect(match).not.toBeNull();
    if (!match) return;

    const size = Number(match[1]);
    const unit = match[2].toLowerCase();
    const sizeInMb = unit === "g" ? size * 1024 : size;

    expect(sizeInMb).toBeGreaterThanOrEqual(25);
  });
});
