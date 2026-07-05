import { ThemeProvider } from "@mui/material/styles";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../app/theme";
import { EditorWorkspace } from "./EditorWorkspace";

const pixiMocks = vi.hoisted(() => ({
  applyRecipe: vi.fn(),
  calculateRecipePreview: vi.fn(() => ({
    allLiquifyStrokes: [],
    displacementScale: { x: 0, y: 0 },
    detailRegionCount: 0,
    eyeMeshTransforms: [],
    eyeOffset: { x: 0, y: 0 },
    eyeScale: { x: 1, y: 1 },
    eyeSkew: 0,
    eyeTransformCount: 0,
    featureLiquifyStrokes: [],
    featureStrokeCount: 0,
    imageOffset: { x: 0, y: 0 },
    imageScale: { x: 1, y: 1 },
    imageSkew: { x: 0, y: 0 },
    jawScale: { x: 1, y: 1 },
    liquifyIntensity: 0,
    manualStrokeCount: 0,
    mouthMeshTransforms: [],
    mouthTransformCount: 0,
    strokeCount: 0,
  })),
  destroy: vi.fn(),
  exportImage: vi.fn(),
  mountPixiStage: vi.fn(),
  setImageUrl: vi.fn(),
}));

vi.mock("./deformation/pixiStage", () => ({
  calculateRecipePreview: pixiMocks.calculateRecipePreview,
  mountPixiStage: pixiMocks.mountPixiStage,
}));

vi.mock("./deformation/animeLandmarkDetector", () => ({
  detectAnimeLandmarks: vi.fn().mockResolvedValue(null),
}));

function renderEditor(props: Partial<React.ComponentProps<typeof EditorWorkspace>> = {}) {
  Object.defineProperty(window, "matchMedia", {
    value: vi.fn().mockImplementation((query: string) => ({
      addEventListener: vi.fn(),
      addListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: false,
      media: query,
      onchange: null,
      removeEventListener: vi.fn(),
      removeListener: vi.fn(),
    })),
    writable: true,
  });

  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }

  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  pixiMocks.setImageUrl.mockResolvedValue(undefined);
  pixiMocks.exportImage.mockResolvedValue(new Blob(["edited-image"], { type: "image/png" }));
  pixiMocks.mountPixiStage.mockResolvedValue({
    applyRecipe: pixiMocks.applyRecipe,
    destroy: pixiMocks.destroy,
    exportImage: pixiMocks.exportImage,
    setImageUrl: pixiMocks.setImageUrl,
  });

  return render(
    <ThemeProvider theme={kigTheme}>
      <EditorWorkspace candidateIndex={1} imageHeight={720} imageUrl="/candidate.webp" imageWidth={1280} {...props} />
    </ThemeProvider>,
  );
}

function mockStageRect(stage: HTMLElement, width = 1280, height = 720) {
  vi.spyOn(stage, "getBoundingClientRect").mockReturnValue({
    bottom: height,
    height,
    left: 0,
    right: width,
    toJSON: () => undefined,
    top: 0,
    width,
    x: 0,
    y: 0,
  });
}

describe("EditorWorkspace annotation controls", () => {
  it("shows a note editor for the selected annotation pin", async () => {
    const onRecipeChange = vi.fn();
    renderEditor({ onRecipeChange });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);

    fireEvent.pointerDown(stage, { clientX: 640, clientY: 360, pointerId: 1 });

    const noteEditor = await screen.findByTestId("annotation-note-input-annotation-1");
    const noteInput = within(noteEditor).getByRole("textbox");
    fireEvent.change(noteInput, { target: { value: "keep left eye highlight" } });

    await waitFor(() =>
      expect(onRecipeChange).toHaveBeenLastCalledWith(
        expect.objectContaining({
          annotations: [expect.objectContaining({ id: "annotation-1", note: "keep left eye highlight" })],
        }),
      ),
    );
  });
});
