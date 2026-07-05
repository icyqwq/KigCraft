import { describe, expect, it } from "vitest";
import { buildCumulativeUserTrend, buildDailyCallTrend } from "./AuditOverviewCharts";
import type { AuditGenerationJob } from "../api/client";

function job(partial: Partial<AuditGenerationJob> & Pick<AuditGenerationJob, "id">): AuditGenerationJob {
  return {
    character_session_id: "session-a",
    created_at: "2026-07-01T10:00:00+00:00",
    generation_mode: "front_design",
    id: partial.id,
    outputs: [],
    phase_label: "",
    progress: 100,
    provider: "codex_bridge",
    references: [],
    requirement_ids: [],
    status: "succeeded",
    token_usage: null,
    updated_at: partial.updated_at ?? partial.created_at ?? "2026-07-01T10:00:00+00:00",
    user_notes: "",
    user_requirements: "",
    ...partial,
  };
}

describe("AuditOverviewCharts trend data", () => {
  it("builds daily call counts", () => {
    const points = buildDailyCallTrend([
      job({ id: "job-1", created_at: "2026-07-01T10:00:00+00:00" }),
      job({ id: "job-2", created_at: "2026-07-01T18:00:00+00:00" }),
      job({ id: "job-3", created_at: "2026-07-02T09:00:00+00:00" }),
    ]);

    expect(points).toEqual([
      { label: "07-01", value: 2 },
      { label: "07-02", value: 1 },
    ]);
  });

  it("builds cumulative user counts by day", () => {
    const points = buildCumulativeUserTrend([
      job({ id: "job-1", character_session_id: "session-a", created_at: "2026-07-01T10:00:00+00:00" }),
      job({ id: "job-2", character_session_id: "session-b", created_at: "2026-07-01T18:00:00+00:00" }),
      job({ id: "job-3", character_session_id: "session-b", created_at: "2026-07-02T09:00:00+00:00" }),
    ]);

    expect(points).toEqual([
      { label: "07-01", value: 2 },
      { label: "07-02", value: 2 },
    ]);
  });
});
