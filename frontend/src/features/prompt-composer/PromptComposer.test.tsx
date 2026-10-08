import { ThemeProvider } from "@mui/material/styles";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../app/theme";
import { PromptComposer } from "./PromptComposer";

function renderComposer(overrides: Partial<Parameters<typeof PromptComposer>[0]> = {}) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });

  render(
    <ThemeProvider theme={kigTheme}>
      <PromptComposer
        freeText=""
        selectedRequirementIds={[]}
        onFreeTextChange={vi.fn()}
        {...overrides}
      />
    </ThemeProvider>,
  );

}

describe("PromptComposer", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("hides prompt presets but warns without clearing an overlong Chinese prompt", () => {
    const overlongPrompt = "画".repeat(301);
    renderComposer({ freeText: overlongPrompt });

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText(/提示词较长/)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue(overlongPrompt);
  });
});
