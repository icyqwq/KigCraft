import { ThemeProvider } from "@mui/material/styles";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../app/theme";
import type { ReferenceSlot } from "../workflow/workflowTypes";
import { ReferenceUploader, type ReferenceSlots } from "./ReferenceUploader";

vi.mock("./FrontReferenceCropper", () => ({
  FrontReferenceCropper: ({
    file,
    onCancel,
    onConfirm,
  }: {
    file: File;
    onCancel: () => void;
    onConfirm: (file: File) => void;
  }) => (
    <div data-testid="front-reference-cropper">
      <span>{file.name}</span>
      <button onClick={() => onConfirm(new File(["cropped"], "front-face.png", { type: "image/png" }))} type="button">
        confirm crop
      </button>
      <button onClick={onCancel} type="button">
        cancel crop
      </button>
    </div>
  ),
}));

function renderUploader({
  onReferenceSlotsChange,
  slots,
}: {
  onReferenceSlotsChange?: ComponentProps<typeof ReferenceUploader>["onReferenceSlotsChange"];
  slots?: ReferenceSlots;
} = {}) {
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

  return render(
    <ThemeProvider theme={kigTheme}>
      <ReferenceUploader slots={slots} onReferenceSlotsChange={onReferenceSlotsChange} />
    </ThemeProvider>,
  );
}

function uploadSlotFile(slotId: string, file: File) {
  const input = screen.getByTestId(`reference-slot-${slotId}`).querySelector('input[type="file"]');
  if (!input) throw new Error(`upload input not found for ${slotId}`);
  fireEvent.change(input, { target: { files: [file] } });
}

describe("ReferenceUploader", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("keeps uploaded reference filenames out of the persistent layout", () => {
    const fileName = "_cirno_touhou_drawn_by_taku_michi_sample-e7633cb42202b87e1e15537a3a1cbb48.jpg";
    renderUploader({
      slots: {
        front: {
          objectKey: "front-reference",
          fileName,
          previewUrl: "blob:front-preview",
        },
      },
    });

    expect(screen.getByTestId("reference-preview-front")).toBeInTheDocument();
    expect(screen.queryByText(fileName)).toBeNull();
    expect(screen.getByTestId("reference-preview-front").getAttribute("title")).toContain(fileName);
  });

  it("renders five fixed reference cards without the removed detail slot", () => {
    renderUploader();

    expect(screen.getByTestId("reference-upload-grid")).toHaveAttribute("data-slot-count", "5");
    expect(screen.getByTestId("reference-slot-front")).toBeInTheDocument();
    expect(screen.getByTestId("reference-slot-side")).toBeInTheDocument();
    expect(screen.getByTestId("reference-slot-back")).toBeInTheDocument();
    expect(screen.getByTestId("reference-slot-expression")).toBeInTheDocument();
    expect(screen.getByTestId("reference-slot-accessory")).toBeInTheDocument();
    expect(screen.queryByTestId("reference-slot-detail")).toBeNull();
  });

  it("maps legacy multi-file uploads into the five supported slots only", () => {
    const onReferenceSlotsChange = vi.fn();
    renderUploader({ onReferenceSlotsChange });

    const files = [
      new File(["front"], "front.webp", { type: "image/webp" }),
      new File(["side"], "side.webp", { type: "image/webp" }),
      new File(["back"], "back.webp", { type: "image/webp" }),
      new File(["expression"], "expression.webp", { type: "image/webp" }),
      new File(["accessory"], "accessory.webp", { type: "image/webp" }),
      new File(["detail"], "detail.webp", { type: "image/webp" }),
    ];

    const legacyInput = screen.getByTestId("reference-upload-button").querySelector('input[type="file"]');
    if (!legacyInput) throw new Error("legacy upload input not found");
    fireEvent.change(legacyInput, { target: { files } });
    expect(onReferenceSlotsChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("confirm crop"));

    const emittedSlots = (onReferenceSlotsChange.mock.lastCall?.[0] ?? []) as ReferenceSlot[];
    expect(emittedSlots).toHaveLength(5);
    expect(emittedSlots.map((slot) => slot.kind)).toEqual(["front", "side", "back", "expression", "accessory"]);
  });

  it("opens the face cropper before replacing the front reference slot", () => {
    const onReferenceSlotsChange = vi.fn();
    renderUploader({ onReferenceSlotsChange });

    const file = new File(["front"], "front.webp", { type: "image/webp" });
    uploadSlotFile("front", file);

    expect(screen.getByTestId("front-reference-cropper")).toBeInTheDocument();
    expect(onReferenceSlotsChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("confirm crop"));

    const emittedSlots = (onReferenceSlotsChange.mock.lastCall?.[0] ?? []) as ReferenceSlot[];
    expect(emittedSlots.find((slot) => slot.kind === "front")).toEqual(
      expect.objectContaining({
        file: expect.objectContaining({ name: "front-face.png", type: "image/png" }),
        fileName: "front-face.png",
        objectKey: "front-face.png",
      }),
    );
  });

  it("keeps the existing front reference when face crop is cancelled", () => {
    const onReferenceSlotsChange = vi.fn();
    renderUploader({
      onReferenceSlotsChange,
      slots: {
        front: {
          fileName: "old-front.png",
          objectKey: "old-front.png",
          previewUrl: "blob:old-front",
        },
      },
    });

    const file = new File(["front"], "new-front.webp", { type: "image/webp" });
    uploadSlotFile("front", file);
    fireEvent.click(screen.getByText("cancel crop"));

    expect(onReferenceSlotsChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("reference-preview-front")).toHaveAttribute("title", "old-front.png");
  });

  it("keeps non-front reference slots immediate", () => {
    const onReferenceSlotsChange = vi.fn();
    renderUploader({ onReferenceSlotsChange });

    const file = new File(["side"], "side.webp", { type: "image/webp" });
    uploadSlotFile("side", file);

    expect(screen.queryByTestId("front-reference-cropper")).toBeNull();
    const emittedSlots = (onReferenceSlotsChange.mock.lastCall?.[0] ?? []) as ReferenceSlot[];
    expect(emittedSlots.find((slot) => slot.kind === "side")).toEqual(
      expect.objectContaining({ file, fileName: "side.webp", objectKey: "side.webp" }),
    );
  });
});


