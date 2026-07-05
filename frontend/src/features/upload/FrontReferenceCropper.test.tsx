import { ThemeProvider } from "@mui/material/styles";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../app/theme";
import type { LocalMaskStroke } from "../editor/localGeneration";
import { FrontReferenceCropper } from "./FrontReferenceCropper";

const cropperMocks = vi.hoisted(() => ({
  createFaceBoxMaskStrokes: vi.fn(),
  cropImageFileWithMask: vi.fn(),
  detectAnimeFaceBox: vi.fn(),
  isReliableFaceBoxDetection: vi.fn(),
  loadImageFromFile: vi.fn(),
}));

vi.mock("../editor/components/LocalMaskLayer", () => ({
  LocalMaskLayer: ({ strokes }: { strokes: readonly LocalMaskStroke[] }) => (
    <div data-testid="front-reference-mask-count">{strokes.length}</div>
  ),
}));

vi.mock("../editor/deformation/animeLandmarkDetector", () => ({
  detectAnimeFaceBox: cropperMocks.detectAnimeFaceBox,
}));

vi.mock("./frontReferenceCrop", () => ({
  createFaceBoxMaskStrokes: cropperMocks.createFaceBoxMaskStrokes,
  cropImageFileWithMask: cropperMocks.cropImageFileWithMask,
  isReliableFaceBoxDetection: cropperMocks.isReliableFaceBoxDetection,
  loadImageFromFile: cropperMocks.loadImageFromFile,
}));

function renderCropper() {
  return render(
    <ThemeProvider theme={kigTheme}>
      <FrontReferenceCropper file={new File(["front"], "front.png", { type: "image/png" })} onCancel={vi.fn()} onConfirm={vi.fn()} />
    </ThemeProvider>,
  );
}

describe("FrontReferenceCropper", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:front"),
      revokeObjectURL: vi.fn(),
    });
    cropperMocks.loadImageFromFile.mockResolvedValue({
      height: 400,
      naturalHeight: 400,
      naturalWidth: 300,
      width: 300,
    } as HTMLImageElement);
    cropperMocks.createFaceBoxMaskStrokes.mockReturnValue([
      { id: "face", mode: "brush", points: [{ x: 0.5, y: 0.5 }], radius: 24 },
    ]);
    cropperMocks.isReliableFaceBoxDetection.mockReturnValue(true);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("renders the mask tool as a dialog with a concise confirm action", () => {
    cropperMocks.detectAnimeFaceBox.mockResolvedValue(null);

    renderCropper();

    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("heading", { name: "确认头部" })).toBeInTheDocument();
    expect(screen.queryByText("确认正脸区域")).toBeNull();
    expect(screen.getByRole("button", { name: /确认/ })).toBeInTheDocument();
    expect(screen.queryByText("使用这张脸")).toBeNull();
  });

  it("uses head wording when automatic detection does not find a usable region", async () => {
    cropperMocks.detectAnimeFaceBox.mockResolvedValue(null);

    renderCropper();

    await waitFor(() => expect(screen.getByText("未识别到头部，请手动涂出头部。")).toBeInTheDocument());
  });

  it("tells users to include hair and accessories in the reference mask", () => {
    cropperMocks.detectAnimeFaceBox.mockResolvedValue(null);

    renderCropper();

    expect(screen.getByText(/发型/)).toBeInTheDocument();
    expect(screen.getByText(/发饰/)).toBeInTheDocument();
  });

  it("does not prepaint a large fallback area when MediaPipe has no face match", async () => {
    cropperMocks.detectAnimeFaceBox.mockResolvedValue({
      box: { height: 220, width: 220, x: 40, y: 40 },
      imageHeight: 400,
      imageWidth: 300,
      score: 0,
      usedFallback: true,
    });

    renderCropper();

    await waitFor(() => expect(screen.getByTestId("front-reference-crop-warning")).toBeInTheDocument());
    expect(cropperMocks.createFaceBoxMaskStrokes).not.toHaveBeenCalled();
    expect(screen.getByTestId("front-reference-mask-count")).toHaveTextContent("0");
  });

  it("does not prepaint off-target MediaPipe detections", async () => {
    cropperMocks.isReliableFaceBoxDetection.mockReturnValue(false);
    cropperMocks.loadImageFromFile.mockResolvedValue({
      height: 884,
      naturalHeight: 884,
      naturalWidth: 940,
      width: 940,
    } as HTMLImageElement);
    cropperMocks.detectAnimeFaceBox.mockResolvedValue({
      box: { height: 360, width: 360, x: 10, y: 250 },
      imageHeight: 884,
      imageWidth: 940,
      score: 0.92,
      usedFallback: false,
    });

    renderCropper();

    await waitFor(() => expect(screen.getByTestId("front-reference-crop-warning")).toBeInTheDocument());
    expect(cropperMocks.createFaceBoxMaskStrokes).not.toHaveBeenCalled();
    expect(screen.getByTestId("front-reference-mask-count")).toHaveTextContent("0");
  });

  it("prevents the preview image from being dragged while painting", () => {
    cropperMocks.detectAnimeFaceBox.mockResolvedValue(null);

    renderCropper();

    const image = screen.getByAltText("头部参考裁剪预览");
    expect(image).toHaveAttribute("draggable", "false");
    expect(fireEvent.dragStart(image)).toBe(false);
  });
});
