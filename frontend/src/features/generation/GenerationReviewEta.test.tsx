import { ThemeProvider } from "@mui/material/styles";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../app/theme";
import type { GenerationJob, JobEvent } from "../../api/client";
import i18n from "../../i18n";
import { DEFAULT_LOCALE } from "../../i18n/locales";
import { GenerationReview } from "./GenerationReview";

describe("GenerationReview ETA", () => {
  beforeEach(async () => {
    window.localStorage.clear();
    await i18n.changeLanguage(DEFAULT_LOCALE);
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-28T12:01:30Z"));
  });

  afterEach(async () => {
    cleanup();
    window.localStorage.clear();
    vi.useRealTimers();
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it("shows remaining time while generating even when history is empty", () => {
    render(
      <ThemeProvider theme={kigTheme}>
        <GenerationReview
          events={[
            {
              created_at: "2026-06-28T12:00:00Z",
              message: "generating",
              payload: {},
              progress: 45,
              sequence: 1,
              type: "generating",
            },
          ]}
          job={makeGeneratingJob()}
          selectedCandidateIndex={null}
          onRefresh={vi.fn()}
          onSelectCandidate={vi.fn()}
        />
      </ThemeProvider>,
    );

    expect(screen.getByTestId("generation-elapsed")).toBeInTheDocument();
    expect(screen.getByTestId("generation-eta")).toBeInTheDocument();
    expect(screen.getByTestId("generation-eta").textContent).toContain("5");
  });
});

function makeGeneratingJob(): GenerationJob {
  return {
    accepted_output_index: null,
    character_session_id: "session-1",
    expected_output_count: 1,
    generation_mode: "front_design",
    id: "job-123456",
    outputs: [],
    phase_label: "generating",
    progress: 45,
    provider: "fixture",
    queue_position: null,
    status: "generating",
    token_usage: null,
  };
}
