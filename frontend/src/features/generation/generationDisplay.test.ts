import { beforeEach, describe, expect, it } from "vitest";
import i18n from "../../i18n";
import { formatGenerationMessage } from "./generationDisplay";
import { resolveGenerationError } from "./resolveUserMessage";

describe("formatGenerationMessage", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh-CN");
  });

  it("shows a readable server failure when Codex returns no manifest", () => {
    expect(
      formatGenerationMessage(
        "FileNotFoundError: /runtime/codex/session/job/manifest.json",
      ),
    ).toContain("没返回图片");
    expect(formatGenerationMessage("FileNotFoundError: /runtime/codex/session/job/manifest.json")).toContain(
      "(´；ω；`)",
    );
  });

  it("shows a readable server failure when image generation returns no image", () => {
    expect(
      formatGenerationMessage(
        "Generation service did not return an image. Please retry later.",
      ),
    ).toContain("没返回图片");
  });

  it("explains Codex API quota exhaustion without ambiguous wording", () => {
    const message = formatGenerationMessage({ code: "codex_usage_low", remaining_percent: 12, reset_wait_seconds: 3600 });

    expect(message).toContain("额度用完");
    expect(message).toContain("(´;ω;`)");
    expect(message).not.toContain("/不足");
    expect(formatGenerationMessage("codex_usage_low remaining_percent=12 reset_wait_seconds=3600")).toBe(message);
  });

  it("resolves backend message codes", () => {
    expect(formatGenerationMessage("queued")).toContain("排队中");
    expect(formatGenerationMessage("generating")).toContain("生成");
    expect(formatGenerationMessage("codex_auth_missing")).toContain("还没登录");
    expect(formatGenerationMessage("candidate_ready:2")).toContain("结果 2");
    expect(formatGenerationMessage("candidate_progress:2/4")).toContain("2/4");
  });

  it("explains daily generation quota exhaustion without account-tier wording", () => {
    const message = resolveGenerationError({
      status: 429,
      detail: {
        code: "generation_daily_quota_exceeded",
        limit: 20,
        used: 20,
        remaining: 0,
        reset_at: "2026-07-10T06:00:00+08:00",
      },
    });

    expect(message).toContain("今日生成额度");
    expect(message).not.toMatch(/premium|normal|advanced|高级|普通/i);
  });

  it("shows the provider reason when content policy blocks generation", () => {
    const message = formatGenerationMessage(
      "content_policy:reason=The request includes a real person",
    );

    expect(message).not.toContain("content_policy");
    expect(message).toContain("The request includes a real person");
  });

});
