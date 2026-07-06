import { ThemeProvider } from "@mui/material/styles";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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

const animeLandmarkerMocks = vi.hoisted(() => ({
  detectAnimeLandmarks: vi.fn().mockResolvedValue(null),
  warmupAnimeLandmarkDetector: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("./deformation/pixiStage", () => ({
  calculateRecipePreview: pixiMocks.calculateRecipePreview,
  mountPixiStage: pixiMocks.mountPixiStage,
}));

vi.mock("./deformation/animeLandmarkDetector", () => ({
  detectAnimeLandmarks: animeLandmarkerMocks.detectAnimeLandmarks,
  warmupAnimeLandmarkDetector: animeLandmarkerMocks.warmupAnimeLandmarkDetector,
}));

vi.mock("react-konva", () => ({
  Arrow: (props: Record<string, unknown>) => <div data-testid="annotation-arrow" data-points={String(props.points)} />,
  Circle: () => <div data-testid="annotation-circle" />,
  Layer: ({ children }: { children: React.ReactNode }) => <div data-testid="annotation-layer">{children}</div>,
  Rect: (props: Record<string, unknown>) => (
    <div data-height={String(props.height)} data-testid="annotation-rect" data-width={String(props.width)} />
  ),
  Stage: ({ children, height, width }: { children: React.ReactNode; height: number; width: number }) => (
    <div data-height={height} data-testid="annotation-stage" data-width={width}>
      {children}
    </div>
  ),
  Text: () => <div data-testid="annotation-text" />,
}));

function renderEditor(props: Partial<React.ComponentProps<typeof EditorWorkspace>> = {}) {
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
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:editor-export") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
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
      <EditorWorkspace candidateIndex={2} imageHeight={720} imageUrl="/candidate.webp" imageWidth={1280} {...props} />
    </ThemeProvider>,
  );
}

function getSliderThumb(testId: string) {
  const root = screen.getByTestId(testId);
  return (root.querySelector('input[type="range"]') ?? root.querySelector('[role="slider"]') ?? root) as HTMLElement;
}

function moveSlider(testId: string, key: "ArrowLeft" | "ArrowRight", times: number) {
  const slider = getSliderThumb(testId) as HTMLInputElement;
  const min = Number(slider.getAttribute("min") ?? -1);
  const max = Number(slider.getAttribute("max") ?? 1);
  const step = Number(slider.getAttribute("step") ?? 1);
  const direction = key === "ArrowRight" ? 1 : -1;
  slider.focus();
  for (let index = 0; index < times; index += 1) {
    const currentValue = Number(slider.value || slider.getAttribute("value") || 0);
    const nextValue = Math.min(max, Math.max(min, currentValue + step * direction));
    fireEvent.input(slider, { target: { value: String(nextValue) } });
  }
  fireEvent.blur(slider);
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

function mockLocalMaskCanvas(hasPaint: boolean, canvasSizes: Array<{ height: number; width: number }> = []) {
  const originalGetContext = HTMLCanvasElement.prototype.getContext;
  const originalToBlob = HTMLCanvasElement.prototype.toBlob;
  const context = {
    arc: vi.fn(),
    beginPath: vi.fn(),
    clearRect: vi.fn(),
    fill: vi.fn(),
    fillStyle: "",
    getImageData: vi.fn(() => {
      const data = new Uint8ClampedArray(64 * 64 * 4);
      if (hasPaint) data[3] = 255;
      return { data };
    }),
    globalCompositeOperation: "source-over",
    lineCap: "round",
    lineJoin: "round",
    lineTo: vi.fn(),
    lineWidth: 1,
    moveTo: vi.fn(),
    stroke: vi.fn(),
    strokeStyle: "",
  };
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: vi.fn(() => context),
  });
  Object.defineProperty(HTMLCanvasElement.prototype, "toBlob", {
    configurable: true,
    value: vi.fn(function toBlob(this: HTMLCanvasElement, callback: BlobCallback) {
      canvasSizes.push({ height: this.height, width: this.width });
      callback(new Blob(["mask"], { type: "image/png" }));
    }),
  });
  return () => {
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: originalGetContext,
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "toBlob", {
      configurable: true,
      value: originalToBlob,
    });
  };
}

function mockBlobImageDimensions(width: number, height: number) {
  const originalImage = globalThis.Image;
  class ImageStub {
    onerror: (() => void) | null = null;
    onload: (() => void) | null = null;
    naturalHeight = height;
    naturalWidth = width;
    set src(_value: string) {
      this.onload?.();
    }
  }
  vi.stubGlobal("Image", ImageStub);
  return () => vi.stubGlobal("Image", originalImage);
}

function firePointerEvent(
  target: HTMLElement,
  type: "pointerdown" | "pointermove" | "pointerup",
  init: {
    clientX?: number;
    clientY?: number;
    pointerId: number;
    pointerType?: string;
  },
) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, {
    clientX: init.clientX ?? 0,
    clientY: init.clientY ?? 0,
    pointerId: init.pointerId,
    pointerType: init.pointerType ?? "touch",
  });
  fireEvent(target, event);
}

describe("EditorWorkspace", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("switches tools and toggles the annotation overlay", () => {
    renderEditor();

    expect(screen.getByTestId("active-editor-tool")).toHaveTextContent("鏍囨敞");
    expect(screen.getByTestId("annotation-stage")).toBeInTheDocument();
    expect(screen.queryByTestId("editor-tool-details")).toBeNull();

    fireEvent.click(screen.getByTestId("editor-tool-face"));

    expect(screen.getByTestId("active-editor-tool")).toHaveTextContent("鑴稿瀷");
    expect(screen.queryByTestId("annotation-stage")).toBeNull();
  });

  it("adds annotation marks from canvas clicks", async () => {
    const onRecipeChange = vi.fn();
    renderEditor({ onRecipeChange });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);

    fireEvent.pointerDown(stage, { clientX: 960, clientY: 180 });

    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenLastCalledWith(expect.objectContaining({
        annotations: [
          expect.objectContaining({
            id: "annotation-1",
            kind: "callout",
            x: 0.75,
            y: 0.25,
          }),
        ],
      })),
    );
    expect(onRecipeChange).toHaveBeenLastCalledWith(expect.objectContaining({
      annotations: [expect.objectContaining({ x: 0.75, y: 0.25 })],
    }));
    expect(screen.getByTestId("annotation-mark-count")).toHaveTextContent("1");
    expect(screen.getByTestId("annotation-pin-1")).toHaveTextContent("1");
    expect(screen.getByTestId("annotation-pin-1")).toHaveStyle({ height: "12px", width: "12px" });
  });

  it("uses two-finger gestures to pan and zoom the editor viewport without adding annotations", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);

    firePointerEvent(stage, "pointerdown", { clientX: 400, clientY: 300, pointerId: 1 });
    firePointerEvent(stage, "pointerdown", { clientX: 800, clientY: 300, pointerId: 2 });
    firePointerEvent(stage, "pointermove", { clientX: 360, clientY: 310, pointerId: 1 });
    firePointerEvent(stage, "pointermove", { clientX: 920, clientY: 350, pointerId: 2 });
    firePointerEvent(stage, "pointerup", { pointerId: 1 });
    firePointerEvent(stage, "pointerup", { pointerId: 2 });

    expect(screen.queryByTestId("annotation-pin-1")).toBeNull();
    await waitFor(() => expect(screen.getByText("140%")).toBeInTheDocument());
  });

  it("opens a long-press save menu on the editor image and saves through the existing export flow", async () => {
    const onSave = vi.fn();
    renderEditor({ onSave });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));
    vi.useFakeTimers();
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);

    firePointerEvent(stage, "pointerdown", { clientX: 640, clientY: 360, pointerId: 1 });
    await act(async () => {
      vi.advanceTimersByTime(620);
    });
    vi.useRealTimers();

    expect(screen.getByTestId("editor-save-menu")).toBeInTheDocument();
    expect(screen.queryByTestId("annotation-pin-1")).toBeNull();

    fireEvent.click(screen.getByTestId("editor-save-menu-save"));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
        annotationPrompt: "",
        imageBlob: expect.any(Blob),
      })),
    );
    expect(pixiMocks.exportImage).toHaveBeenCalled();
  });

  it("edits notes for numbered annotation pins from the side panel", async () => {
    const onRecipeChange = vi.fn();
    renderEditor({ onRecipeChange });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);

    fireEvent.pointerDown(stage, { clientX: 640, clientY: 360 });
    const noteInput = await screen.findByTestId("annotation-note-input-annotation-1");
    fireEvent.change(noteInput, { target: { value: "keep left eye highlight" } });

    await waitFor(() =>
      expect(onRecipeChange).toHaveBeenLastCalledWith(expect.objectContaining({
        annotations: [expect.objectContaining({ id: "annotation-1", note: "keep left eye highlight" })],
      })),
    );
  });

  it("cleans empty annotation pins and sends annotation text when regenerating", async () => {
    const onRegenerate = vi.fn();
    renderEditor({ onRegenerate });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);

    fireEvent.pointerDown(stage, { clientX: 320, clientY: 360 });
    fireEvent.pointerDown(stage, { clientX: 960, clientY: 180 });
    const noteInput = await screen.findByTestId("annotation-note-input-annotation-2");
    fireEvent.change(noteInput, { target: { value: "keep right eye highlight" } });

    fireEvent.click(screen.getByTestId("editor-regenerate"));

    await waitFor(() =>
      expect(onRegenerate).toHaveBeenCalledWith(expect.objectContaining({
        annotationPrompt: "鏍囨敞 1锛?5%, 25%锛夛細keep right eye highlight",
        recipe: expect.objectContaining({
          annotations: [
            expect.objectContaining({
              id: "annotation-1",
              note: "keep right eye highlight",
              x: 0.75,
              y: 0.25,
            }),
          ],
        }),
      })),
    );
  });

  it("sends supplemental prompt text without requiring an uploaded reference image", async () => {
    const onRegenerate = vi.fn();
    renderEditor({ onRegenerate });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    const promptInput = screen.getByPlaceholderText("例如：嘴型更委屈，眼神更柔和；上传图片时也会作为参考图说明");
    expect(promptInput).not.toBeDisabled();
    fireEvent.change(promptInput, { target: { value: "嘴型更委屈，眼神更柔和" } });
    fireEvent.click(screen.getByTestId("editor-regenerate"));

    await waitFor(() =>
      expect(onRegenerate).toHaveBeenCalledWith(expect.objectContaining({
        extraReference: undefined,
        promptNote: "嘴型更委屈，眼神更柔和",
      })),
    );
  });

  it("switches to the local generation tool when available", async () => {
    const restoreCanvas = mockLocalMaskCanvas(false);
    try {
      renderEditor({ onLocalGenerate: vi.fn() });
      await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

      fireEvent.click(screen.getByTestId("editor-tool-local-generate"));

      expect(screen.getByTestId("local-generate-controls")).toBeInTheDocument();
      expect(screen.getByTestId("local-mask-layer")).toBeInTheDocument();
      expect(screen.getByTestId("editor-local-generate-submit")).toBeDisabled();
    } finally {
      restoreCanvas();
    }
  });

  it("renders the local generation brush preview as a circle", async () => {
    const restoreCanvas = mockLocalMaskCanvas(false);
    try {
      renderEditor({ onLocalGenerate: vi.fn() });
      await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

      fireEvent.click(screen.getByTestId("editor-tool-local-generate"));
      const stage = screen.getByTestId("editor-stage");
      mockStageRect(stage);
      fireEvent.pointerDown(stage, { clientX: 640, clientY: 360, pointerId: 1, pointerType: "mouse" });

      expect(screen.getByTestId("local-mask-brush-preview")).toHaveStyle({ borderRadius: "50%" });
    } finally {
      restoreCanvas();
    }
  });

  it("uses only an uploaded local generation reference", async () => {
    const restoreCanvas = mockLocalMaskCanvas(true);
    const restoreImage = mockBlobImageDimensions(1280, 720);
    const onLocalGenerate = vi.fn();
    try {
      renderEditor({
        localReferenceOptions: [
          { key: "front:references/upload-1/front.webp", label: "正脸参考" },
          { key: "detail:references/upload-1/eyes.webp", label: "眼睛参考" },
        ],
        onLocalGenerate,
      });
      await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

      fireEvent.click(screen.getByTestId("editor-tool-local-generate"));
      expect(screen.queryByTestId("editor-local-reference-front:references/upload-1/front.webp")).toBeNull();
      expect(screen.queryByTestId("editor-local-reference-detail:references/upload-1/eyes.webp")).toBeNull();
      const uploadFile = new File(["ref"], "mouth.png", { type: "image/png" });
      fireEvent.change(screen.getByTestId("editor-local-reference-file-input"), {
        target: { files: [uploadFile] },
      });

      const stage = screen.getByTestId("editor-stage");
      mockStageRect(stage);
      fireEvent.pointerDown(stage, { clientX: 640, clientY: 360, pointerId: 1, pointerType: "mouse" });
      fireEvent.pointerMove(stage, { clientX: 700, clientY: 380, pointerId: 1, pointerType: "mouse" });
      fireEvent.pointerUp(stage, { pointerId: 1, pointerType: "mouse" });

      await waitFor(() => expect(screen.getByTestId("editor-local-generate-submit")).toBeEnabled());
      fireEvent.click(screen.getByTestId("editor-local-generate-submit"));

      await waitFor(() => expect(onLocalGenerate).toHaveBeenCalled());
      expect(onLocalGenerate).toHaveBeenCalledWith(expect.objectContaining({
        selectedReferenceKeys: [],
        uploadedReferences: [{ description: "", file: uploadFile }],
      }));
    } finally {
      restoreImage();
      restoreCanvas();
    }
  });

  it("submits local generation payload after painting a mask", async () => {
    const restoreCanvas = mockLocalMaskCanvas(true);
    const restoreImage = mockBlobImageDimensions(1280, 720);
    const onLocalGenerate = vi.fn();
    try {
      renderEditor({
        localReferenceOptions: [{ key: "front:references/upload-1/front.webp", label: "正脸参考" }],
        onLocalGenerate,
      });
      await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

      fireEvent.click(screen.getByTestId("editor-tool-local-generate"));
      const noteInput = screen.getByTestId("editor-local-generate-note").querySelector("textarea");
      if (!noteInput) throw new Error("local generation note input not found");
      fireEvent.change(noteInput, { target: { value: "make mouth smaller" } });
      const uploadFile = new File(["ref"], "mouth.png", { type: "image/png" });
      fireEvent.change(screen.getByTestId("editor-local-reference-file-input"), {
        target: { files: [uploadFile] },
      });
      expect(screen.queryByTestId("editor-local-reference-description")).toBeNull();
      const stage = screen.getByTestId("editor-stage");
      mockStageRect(stage);
      fireEvent.pointerDown(stage, { clientX: 640, clientY: 360, pointerId: 1 });
      fireEvent.pointerMove(stage, { clientX: 700, clientY: 380, pointerId: 1 });
      fireEvent.pointerUp(stage, { pointerId: 1 });

      await waitFor(() => expect(screen.getByTestId("editor-local-generate-submit")).toBeEnabled());
      fireEvent.click(screen.getByTestId("editor-local-generate-submit"));

      await waitFor(() => expect(onLocalGenerate).toHaveBeenCalled());
      expect(onLocalGenerate).toHaveBeenCalledWith(expect.objectContaining({
        baseImageBlob: expect.any(Blob),
        editNote: "make mouth smaller",
        maskImageBlob: expect.any(Blob),
        selectedReferenceKeys: [],
        uploadedReferences: [{ description: "make mouth smaller", file: uploadFile }],
      }));
    } finally {
      restoreImage();
      restoreCanvas();
    }
  });

  it("shows a real error when local generation export fails", async () => {
    const restoreCanvas = mockLocalMaskCanvas(true);
    const onLocalGenerate = vi.fn();
    try {
      pixiMocks.exportImage.mockRejectedValueOnce(new Error("export failed"));
      renderEditor({ onLocalGenerate });
      await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

      fireEvent.click(screen.getByTestId("editor-tool-local-generate"));
      const stage = screen.getByTestId("editor-stage");
      mockStageRect(stage);
      fireEvent.pointerDown(stage, { clientX: 640, clientY: 360, pointerId: 1 });
      fireEvent.pointerUp(stage, { pointerId: 1 });

      await waitFor(() => expect(screen.getByTestId("editor-local-generate-submit")).toBeEnabled());
      fireEvent.click(screen.getByTestId("editor-local-generate-submit"));

      expect(await screen.findByTestId("editor-local-generate-error")).toHaveTextContent("export failed");
      expect(onLocalGenerate).not.toHaveBeenCalled();
    } finally {
      restoreCanvas();
    }
  });

  it("exports the local mask at the same size as the edited base image", async () => {
    const canvasSizes: Array<{ height: number; width: number }> = [];
    const restoreCanvas = mockLocalMaskCanvas(true, canvasSizes);
    const restoreImage = mockBlobImageDimensions(600, 900);
    const onLocalGenerate = vi.fn();
    try {
      pixiMocks.exportImage.mockResolvedValueOnce(new Blob(["edited-image"], { type: "image/png" }));
      renderEditor({ onLocalGenerate });
      await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

      fireEvent.click(screen.getByTestId("editor-tool-local-generate"));
      const stage = screen.getByTestId("editor-stage");
      mockStageRect(stage, 1280, 720);
      fireEvent.pointerDown(stage, { clientX: 320, clientY: 360, pointerId: 3, pointerType: "mouse" });
      fireEvent.pointerMove(stage, { clientX: 380, clientY: 380, pointerId: 3, pointerType: "mouse" });
      fireEvent.pointerUp(stage, { clientX: 380, clientY: 380, pointerId: 3, pointerType: "mouse" });

      await waitFor(() => expect(screen.getByTestId("editor-local-generate-submit")).toBeEnabled());
      fireEvent.click(screen.getByTestId("editor-local-generate-submit"));

      await waitFor(() => expect(onLocalGenerate).toHaveBeenCalledTimes(1));
      expect(canvasSizes.at(-1)).toEqual({ height: 900, width: 600 });
    } finally {
      restoreImage();
      restoreCanvas();
    }
  });

  it("selects annotation pins so they can be dragged and deleted", async () => {
    const onRecipeChange = vi.fn();
    renderEditor({ onRecipeChange });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);

    fireEvent.pointerDown(stage, { clientX: 640, clientY: 360, pointerId: 1 });
    const pin = await screen.findByTestId("annotation-pin-1");

    fireEvent.pointerDown(pin, { clientX: 640, clientY: 360, pointerId: 2 });
    fireEvent.pointerMove(stage, { clientX: 960, clientY: 180, pointerId: 2 });
    fireEvent.pointerUp(stage, { pointerId: 2 });

    await waitFor(() =>
      expect(onRecipeChange).toHaveBeenLastCalledWith(expect.objectContaining({
        annotations: [
          expect.objectContaining({
            id: "annotation-1",
            x: 0.75,
            y: 0.25,
          }),
        ],
      })),
    );
    expect(screen.getByTestId("annotation-pin-1")).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByTestId("annotation-delete-annotation-1"));

    await waitFor(() =>
      expect(onRecipeChange).toHaveBeenLastCalledWith(expect.objectContaining({
        annotations: [],
      })),
    );
    expect(screen.queryByTestId("annotation-pin-1")).toBeNull();
  });

  it("mounts Pixi with the selected image and sizes the annotation overlay from candidate dimensions", async () => {
    renderEditor({ imageHeight: 900, imageUrl: "/generated/candidate-2.webp", imageWidth: 600 });

    await waitFor(() => expect(pixiMocks.mountPixiStage).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/generated/candidate-2.webp"));
    expect(screen.getByTestId("editor-stage")).toHaveStyle({ aspectRatio: "600 / 900" });
    expect(screen.getByTestId("annotation-stage")).toHaveAttribute("data-width", "600");
    expect(screen.getByTestId("annotation-stage")).toHaveAttribute("data-height", "900");
  });

  it("places the parameter controls on the right side of the preview area", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    expect(screen.getByTestId("editor-shell")).toHaveAttribute("data-parameters-position", "right");
    expect(screen.getByTestId("editor-controls-panel")).toHaveStyle({ gridColumn: "3" });
  });

  it("uses uploaded image natural dimensions to frame the editor stage when candidate dimensions are missing", async () => {
    renderEditor({ imageHeight: undefined, imageWidth: undefined });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));
    const image = screen.getByTestId("editor-base-image");

    Object.defineProperty(image, "naturalWidth", { configurable: true, value: 600 });
    Object.defineProperty(image, "naturalHeight", { configurable: true, value: 800 });
    fireEvent.load(image);

    expect(screen.getByTestId("editor-stage")).toHaveStyle({ aspectRatio: "600 / 800" });
    expect(screen.getByTestId("annotation-stage")).toHaveAttribute("data-width", "600");
    expect(screen.getByTestId("annotation-stage")).toHaveAttribute("data-height", "800");
  });

  it("uses the latest image URL when Pixi mount resolves after a rerender", async () => {
    let resolveMount:
      | ((stage: {
          applyRecipe: typeof pixiMocks.applyRecipe;
          destroy: typeof pixiMocks.destroy;
          setImageUrl: typeof pixiMocks.setImageUrl;
        }) => void)
      | undefined;
    pixiMocks.mountPixiStage.mockReturnValue(
      new Promise((resolve) => {
        resolveMount = resolve;
      }),
    );

    const { rerender } = renderEditor({ imageUrl: "/generated/candidate-a.webp" });

    rerender(
      <ThemeProvider theme={kigTheme}>
        <EditorWorkspace candidateIndex={2} imageHeight={720} imageUrl="/generated/candidate-b.webp" imageWidth={1280} />
      </ThemeProvider>,
    );

    resolveMount?.({
      applyRecipe: pixiMocks.applyRecipe,
      destroy: pixiMocks.destroy,
      setImageUrl: pixiMocks.setImageUrl,
    });

    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/generated/candidate-b.webp"));
    expect(pixiMocks.setImageUrl).not.toHaveBeenCalledWith("/generated/candidate-a.webp");
  });

  it("applies face and eye slider edits to Pixi and saves them in the recipe payload", async () => {
    const onSave = vi.fn();
    renderEditor({ onSave });

    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-face"));
    moveSlider("face-control-faceWidth", "ArrowLeft", 24);

    fireEvent.click(screen.getByTestId("editor-tool-eyes"));
    moveSlider("eye-control-eyeSize", "ArrowRight", 19);
    moveSlider("eye-control-eyeHeight", "ArrowRight", 7);

    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenLastCalledWith(expect.objectContaining({
        face: expect.objectContaining({ faceWidth: -0.048 }),
        eyes: expect.objectContaining({ eyeHeight: 0.021, eyeSize: 0.057 }),
      })),
    );

    fireEvent.click(screen.getByTestId("editor-save"));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
        annotationPrompt: "",
        fileName: expect.stringMatching(/^kigcraft-edit-\d+\.png$/),
        imageBlob: expect.any(Blob),
        recipe: expect.objectContaining({
          face: expect.objectContaining({ faceWidth: -0.048 }),
          eyes: expect.objectContaining({ eyeHeight: 0.021, eyeSize: 0.057 }),
        }),
      })),
    );
    expect(pixiMocks.exportImage).toHaveBeenCalled();
  });

  it("limits deformation sliders and exposes individual reset controls", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-face"));
    const faceWidthSlider = getSliderThumb("face-control-faceWidth");
    expect(faceWidthSlider).toHaveAttribute("aria-valuemin", "-1");
    expect(faceWidthSlider).toHaveAttribute("aria-valuemax", "1");
    const faceLengthSlider = getSliderThumb("face-control-faceLength");
    expect(faceLengthSlider).toHaveAttribute("aria-valuemin", "-1");
    expect(faceLengthSlider).toHaveAttribute("aria-valuemax", "1");

    fireEvent.click(screen.getByLabelText("扩大参数范围"));
    moveSlider("face-control-faceLength", "ArrowRight", 10);
    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenLastCalledWith(expect.objectContaining({
        face: expect.objectContaining({ faceLength: 0.06 }),
      })),
    );

    moveSlider("face-control-faceWidth", "ArrowRight", 22);
    moveSlider("face-control-faceLength", "ArrowLeft", 18);
    fireEvent.click(screen.getByTestId("face-control-faceWidth-reset"));
    fireEvent.click(screen.getByTestId("face-control-faceLength-reset"));

    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenLastCalledWith(expect.objectContaining({
        face: expect.objectContaining({ faceLength: 0, faceWidth: 0 }),
      })),
    );

    fireEvent.click(screen.getByTestId("editor-tool-eyes"));
    expect(screen.queryByText("鍗曚晶璋冩暣")).toBeNull();
    expect(getSliderThumb("eye-control-eyeSize")).toHaveAttribute("aria-valuemin", "-1");
    expect(getSliderThumb("eye-control-eyeSize")).toHaveAttribute("aria-valuemax", "1");
    expect(getSliderThumb("eye-control-eyeWidth")).toHaveAttribute("aria-valuemin", "-1");
    expect(getSliderThumb("eye-control-eyeWidth")).toHaveAttribute("aria-valuemax", "1");

    fireEvent.click(screen.getByLabelText("扩大参数范围"));
    moveSlider("eye-control-eyeSize", "ArrowRight", 10);
    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenLastCalledWith(expect.objectContaining({
        eyes: expect.objectContaining({ eyeSize: 0.06 }),
      })),
    );

    moveSlider("eye-control-eyeWidth", "ArrowRight", 12);
    fireEvent.click(screen.getByTestId("eye-control-eyeWidth-reset"));

    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenLastCalledWith(expect.objectContaining({
        eyes: expect.objectContaining({ eyeWidth: 0 }),
      })),
    );
  });

  it("resets all editor adjustments from the global reset button", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-face"));
    moveSlider("face-control-vLine", "ArrowRight", 16);
    fireEvent.click(screen.getByTestId("editor-tool-eyes"));
    moveSlider("eye-control-eyeSize", "ArrowRight", 12);
    fireEvent.click(screen.getByTestId("editor-tool-liquify"));
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);
    fireEvent.pointerDown(stage, { clientX: 320, clientY: 540 });

    fireEvent.click(screen.getByTestId("editor-reset-all"));

    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenLastCalledWith(expect.objectContaining({
        face: expect.objectContaining({ vLine: 0 }),
        eyes: expect.objectContaining({ eyeSize: 0 }),
        liquify: [],
      })),
    );
  });

  it("renders landmark markers when editing face or eyes", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-face"));

    expect(screen.getByTestId("landmark-leftEye")).toBeInTheDocument();
    expect(screen.getByTestId("landmark-rightEye")).toBeInTheDocument();
    expect(screen.getByTestId("landmark-chin")).toBeInTheDocument();
    expect(screen.getByTestId("landmark-jawLeft")).toBeInTheDocument();
    expect(screen.getByTestId("landmark-jawRight")).toBeInTheDocument();
  });

  it("dismisses the landmark hint after the first canvas interaction", async () => {
    renderEditor({
      initialLandmarks: {
        chin: { x: 0.5, y: 0.68 },
        jawLeft: { x: 0.34, y: 0.58 },
        jawRight: { x: 0.66, y: 0.58 },
        leftEye: { x: 0.42, y: 0.42 },
        rightEye: { x: 0.58, y: 0.42 },
      },
    });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-face"));
    await waitFor(() => expect(screen.getByTestId("landmark-correction-hint")).toBeInTheDocument());

    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);
    fireEvent.pointerDown(stage, { clientX: 640, clientY: 360, pointerId: 1 });

    expect(screen.queryByTestId("landmark-correction-hint")).toBeNull();

    fireEvent.click(screen.getByTestId("editor-tool-eyes"));
    fireEvent.click(screen.getByTestId("editor-tool-face"));

    expect(screen.queryByTestId("landmark-correction-hint")).toBeNull();
  });

  it("hides and shows landmark markers from the face tool controls", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-face"));
    expect(screen.getByTestId("landmark-leftEye")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("landmark-visibility-toggle"));
    expect(screen.queryByTestId("landmark-leftEye")).toBeNull();

    fireEvent.click(screen.getByTestId("landmark-visibility-toggle"));
    expect(screen.getByTestId("landmark-leftEye")).toBeInTheDocument();
  });

  it("keeps fallback landmark markers visible when recognition has no match", async () => {
    const onRecipeChange = vi.fn();
    renderEditor({ onRecipeChange });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-face"));
    fireEvent.click(screen.getByTestId("editor-recognize-face"));

    expect(screen.getByTestId("landmark-leftEye")).toBeInTheDocument();
    expect(screen.getByTestId("landmark-rightEye")).toBeInTheDocument();
    expect(screen.getByTestId("landmark-chin")).toBeInTheDocument();
    await waitFor(() =>
      expect(onRecipeChange).toHaveBeenLastCalledWith(expect.objectContaining({
        landmarks: expect.objectContaining({
          leftEye: { x: 0.42, y: 0.42 },
        }),
      })),
    );
  });

  it("keeps fallback landmark markers visible when automatic recognition fails", async () => {
    const onRecipeChange = vi.fn();
    animeLandmarkerMocks.detectAnimeLandmarks.mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          setTimeout(() => reject(new Error("wasm unavailable")), 20);
        }),
    );
    renderEditor({ onRecipeChange });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-face"));
    const image = screen.getByTestId("editor-base-image") as HTMLImageElement;
    Object.defineProperty(image, "complete", { configurable: true, value: true });
    Object.defineProperty(image, "naturalWidth", { configurable: true, value: 1280 });
    Object.defineProperty(image, "naturalHeight", { configurable: true, value: 720 });
    fireEvent.load(image);

    expect(screen.getByTestId("landmark-loading-indicator")).toHaveTextContent("正在识别关键点");
    await waitFor(() => expect(screen.getByTestId("landmark-leftEye")).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByTestId("landmark-loading-indicator")).toBeNull());
    expect(screen.getByTestId("landmark-rightEye")).toBeInTheDocument();
    expect(screen.getByTestId("landmark-chin")).toBeInTheDocument();
    await waitFor(() =>
      expect(onRecipeChange).toHaveBeenLastCalledWith(expect.objectContaining({
        landmarks: expect.objectContaining({
          leftEye: { x: 0.42, y: 0.42 },
        }),
      })),
    );
  });

  it("keeps the viewport toolbar controls on one row", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    const toolbar = screen.getByTestId("editor-canvas-toolbar");
    expect(toolbar).toHaveStyle({ overflowX: "hidden" });
    expect(toolbar).toHaveStyle({ flexWrap: "nowrap" });
  });

  it("pans the editor image with right-button mouse drag without opening the save menu", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);
    fireEvent.pointerDown(stage, { button: 2, clientX: 200, clientY: 220, pointerId: 9, pointerType: "mouse" });
    fireEvent.pointerMove(stage, { clientX: 260, clientY: 250, pointerId: 9, pointerType: "mouse" });
    fireEvent.pointerUp(stage, { button: 2, clientX: 260, clientY: 250, pointerId: 9, pointerType: "mouse" });
    fireEvent.contextMenu(stage, { clientX: 260, clientY: 250 });

    expect(screen.getByTestId("editor-transform-layer")).toHaveStyle({
      transform: "translate(60px, 30px) scale(1)",
    });
    expect(screen.queryByTestId("editor-save-menu")).toBeNull();
  });

  it("keeps right-click save menu available when the mouse is not dragged", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);
    fireEvent.pointerDown(stage, { button: 2, clientX: 260, clientY: 250, pointerId: 10, pointerType: "mouse" });
    fireEvent.pointerUp(stage, { button: 2, clientX: 260, clientY: 250, pointerId: 10, pointerType: "mouse" });
    fireEvent.contextMenu(stage, { clientX: 260, clientY: 250 });

    expect(screen.getByTestId("editor-save-menu")).toBeInTheDocument();
  });

  it("lets users drag landmark markers to correct deformation anchors", async () => {
    const onRecipeChange = vi.fn();
    renderEditor({ onRecipeChange });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-face"));
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);
    const leftEye = screen.getByTestId("landmark-leftEye");

    fireEvent.pointerDown(leftEye, { clientX: 538, clientY: 302, pointerId: 3 });
    fireEvent.pointerMove(stage, { clientX: 960, clientY: 180, pointerId: 3 });
    fireEvent.pointerUp(stage, { pointerId: 3 });

    await waitFor(() =>
      expect(onRecipeChange).toHaveBeenLastCalledWith(expect.objectContaining({
        landmarks: expect.objectContaining({
          leftEye: { x: 0.75, y: 0.25 },
        }),
      })),
    );
    expect(screen.getByTestId("landmark-leftEye")).toHaveAttribute("aria-selected", "true");
  });

  it("applies deformation brush drags with direction and distance from pointer movement", async () => {
    const onRegenerate = vi.fn();
    renderEditor({ onRegenerate });

    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-liquify"));
    moveSlider("liquify-radius-slider", "ArrowRight", 24);
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);
    fireEvent.pointerDown(stage, { clientX: 320, clientY: 540, pointerId: 1 });
    fireEvent.pointerMove(stage, { clientX: 448, clientY: 468, pointerId: 1 });
    fireEvent.pointerUp(stage, { pointerId: 1 });

    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenLastCalledWith(expect.objectContaining({
        liquify: [
          expect.objectContaining({
            deltaX: 0.1,
            deltaY: -0.1,
            mode: "warp",
            radius: 96,
            x: 0.3,
            y: 0.7,
          }),
        ],
      })),
    );

    fireEvent.click(screen.getByTestId("editor-regenerate"));

    expect(onRegenerate).toHaveBeenCalledWith(expect.objectContaining({
      liquify: [
        expect.objectContaining({
          deltaX: 0.1,
          deltaY: -0.1,
          mode: "warp",
          radius: 96,
        }),
      ],
    }));
  });

  it("shows the liquify deformation brush cursor while hovering the image area", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-liquify"));
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);

    fireEvent.pointerMove(stage, { clientX: 640, clientY: 360, pointerId: 1, pointerType: "mouse" });

    const preview = screen.getByTestId("liquify-brush-preview");
    expect(preview).toHaveAttribute("data-mode", "warp");
    expect(preview).toHaveAttribute("data-active", "false");

    fireEvent.pointerDown(stage, { clientX: 640, clientY: 360, pointerId: 1, pointerType: "mouse" });
    expect(screen.getByTestId("liquify-brush-preview")).toHaveAttribute("data-active", "true");

    fireEvent.pointerMove(stage, { clientX: 700, clientY: 300, pointerId: 1, pointerType: "mouse" });
    expect(screen.queryAllByTestId(/liquify-stroke-/)).toHaveLength(0);
  });

  it("mirrors warp brush drags with symmetric liquify enabled by default", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-liquify"));
    expect(screen.getByTestId("liquify-symmetry-axis")).toHaveStyle({ left: "50%" });
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);
    mockStageRect(screen.getByTestId("editor-image-viewport"));

    fireEvent.pointerDown(stage, { button: 0, clientX: 256, clientY: 432, pointerId: 1, pointerType: "mouse" });
    fireEvent.pointerMove(stage, { button: 0, clientX: 384, clientY: 360, pointerId: 1, pointerType: "mouse" });
    fireEvent.pointerUp(stage, { button: 0, pointerId: 1, pointerType: "mouse" });

    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenCalledWith(expect.objectContaining({
        liquify: [
          expect.objectContaining({ deltaX: 0.1, deltaY: -0.1, mode: "warp", x: 0.25 }),
          expect.objectContaining({ deltaX: -0.1, deltaY: -0.1, mode: "warp", x: 0.75 }),
        ],
      })),
    );
  });

  it("uses the manually adjusted symmetry axis for mirrored liquify strokes", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-liquify"));
    moveSlider("liquify-symmetry-axis-slider", "ArrowRight", 50);
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);
    mockStageRect(screen.getByTestId("editor-image-viewport"));

    fireEvent.pointerDown(stage, { button: 0, clientX: 256, clientY: 432, pointerId: 1, pointerType: "mouse" });
    fireEvent.pointerMove(stage, { button: 0, clientX: 384, clientY: 360, pointerId: 1, pointerType: "mouse" });
    fireEvent.pointerUp(stage, { button: 0, pointerId: 1, pointerType: "mouse" });

    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenCalledWith(expect.objectContaining({
        liquify: expect.arrayContaining([
          expect.objectContaining({ deltaX: -0.1, mode: "warp", x: 0.85 }),
        ]),
      })),
    );
  });

  it("places and updates one local scale cursor from the liquify controls", async () => {
    renderEditor();
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-liquify"));
    fireEvent.click(screen.getByText("局部缩放"));
    moveSlider("liquify-radius-slider", "ArrowRight", 20);
    moveSlider("liquify-scale-slider", "ArrowLeft", 30);
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);
    fireEvent.pointerDown(stage, { clientX: 640, clientY: 360, pointerId: 1 });

    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenLastCalledWith(expect.objectContaining({
        liquify: [
          expect.objectContaining({ mode: "scale", radius: 92, scale: -0.3, x: 0.5, y: 0.5 }),
        ],
      })),
    );
    expect(screen.getAllByTestId(/liquify-stroke-/)).toHaveLength(1);
  });

  it("adds detail regions and stores detail panel settings", async () => {
    const onSave = vi.fn();
    renderEditor({ onSave });
    await waitFor(() => expect(pixiMocks.setImageUrl).toHaveBeenCalledWith("/candidate.webp"));

    fireEvent.click(screen.getByTestId("editor-tool-details"));
    fireEvent.click(screen.getByTestId("detail-preserve-skin"));
    fireEvent.change(screen.getByTestId("detail-accessory-note"), {
      target: { value: "淇濈暀缁胯壊鎸戞煋鍜岃€虫湹杈圭紭" },
    });
    const stage = screen.getByTestId("editor-stage");
    mockStageRect(stage);
    fireEvent.pointerDown(stage, { clientX: 640, clientY: 360 });

    await waitFor(() =>
      expect(pixiMocks.applyRecipe).toHaveBeenLastCalledWith(expect.objectContaining({
        details: expect.objectContaining({
          accessoryNote: "淇濈暀缁胯壊鎸戞煋鍜岃€虫湹杈圭紭",
          preserveSkinTexture: false,
          regions: [expect.objectContaining({ id: "detail-1", x: 0.5, y: 0.5 })],
        }),
      })),
    );

    fireEvent.click(screen.getByTestId("editor-save"));

    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      details: expect.objectContaining({
        accessoryNote: "淇濈暀缁胯壊鎸戞煋鍜岃€虫湹杈圭紭",
        preserveSkinTexture: false,
        regions: [expect.objectContaining({ x: 0.5, y: 0.5 })],
      }),
    }));
  });
});

