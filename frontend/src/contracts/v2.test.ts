import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { submitTurnSchema } from "./v2";

const fixtures = JSON.parse(readFileSync("../docs/design/fixtures/v2-submit-turn.json", "utf8")) as {
  valid: Record<string, unknown>[];
  invalid: Record<string, unknown>[];
};

describe("shared V2 submission contract", () => {
  it.each(fixtures.valid)("preserves valid user content", (payload) => {
    expect(submitTurnSchema.parse(payload).text).toBe(payload.text);
  });
  it.each(fixtures.invalid)("rejects invalid or server-owned fields", (payload) => {
    expect(submitTurnSchema.safeParse(payload).success).toBe(false);
  });
});
