import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../app/theme";
import { PromptComposer } from "./PromptComposer";

const requirementOptions = [
  {
    id: "soft_eyes",
    group: "眼睛",
    label: "眼神更柔和",
    description: "让眼神更柔和，保留角色识别度。",
    prompt_text: "make the eyes softer while keeping the character identity",
    sort_order: 20,
  },
  {
    id: "round_face",
    group: "脸部风格",
    label: "脸更圆",
    description: "强化圆润脸型。",
    prompt_text: "make the face rounder",
    sort_order: 10,
  },
  {
    id: "product_four_view",
    group: "成品质感",
    label: "四视角成品图",
    description: "输出四视角成品图。",
    prompt_text: "four-view finished product sheet",
    sort_order: 90,
  },
];

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

  const onSelectedRequirementIdsChange = vi.fn();
  const client = new QueryClient();

  render(
    <MantineProvider defaultColorScheme="dark" theme={kigTheme}>
      <QueryClientProvider client={client}>
        <PromptComposer
          freeText=""
          selectedRequirementIds={[]}
          onFreeTextChange={vi.fn()}
          onSelectedRequirementIdsChange={onSelectedRequirementIdsChange}
          options={requirementOptions}
          {...overrides}
        />
      </QueryClientProvider>
    </MantineProvider>,
  );

  return { onSelectedRequirementIdsChange };
}

describe("PromptComposer", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders grouped generation requirements without the rejected label", () => {
    renderComposer();

    expect(screen.getByText("生成要求")).toBeInTheDocument();
    expect(screen.getByText("眼睛")).toBeInTheDocument();
    expect(screen.getByText("眼神更柔和")).toBeInTheDocument();
    expect(screen.queryByText("Prompt 积木")).toBeNull();
    expect(screen.queryByText("成品质感")).toBeNull();
    expect(screen.queryByText("系统固定")).toBeNull();
  });

  it("calls selection change when an option is clicked", () => {
    const { onSelectedRequirementIdsChange } = renderComposer();

    fireEvent.click(screen.getByText("眼神更柔和"));

    expect(onSelectedRequirementIdsChange).toHaveBeenCalledWith(["soft_eyes"]);
  });

  it("hides product quality requirements from the frontend", () => {
    renderComposer();

    expect(screen.queryByTestId("fixed-product-quality")).toBeNull();
    expect(screen.queryByText("成品质感")).toBeNull();
    expect(screen.queryByText("系统固定")).toBeNull();
    expect(screen.queryByText("四视角成品图")).toBeNull();
    expect(screen.queryByRole("button", { name: "四视角成品图" })).toBeNull();
  });
});
