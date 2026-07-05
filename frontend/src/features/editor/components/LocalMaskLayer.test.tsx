import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LocalMaskLayer } from "./LocalMaskLayer";

describe("LocalMaskLayer", () => {
  afterEach(() => cleanup());

  it("keeps mask geometry in image aspect ratio instead of stretching to the host", () => {
    render(
      <div style={{ height: 300, position: "relative", width: 900 }}>
        <LocalMaskLayer
          brushPreview={{ radius: 24, x: 0.5, y: 0.5 }}
          height={300}
          strokes={[{ id: "stroke", mode: "brush", points: [{ x: 0.5, y: 0.5 }], radius: 24 }]}
          width={300}
        />
      </div>,
    );

    const svg = screen.getByTestId("local-mask-svg");
    expect(svg).toHaveAttribute("preserveAspectRatio", "xMidYMid meet");

    const preview = screen.getByTestId("local-mask-brush-preview");
    expect(preview.tagName.toLowerCase()).toBe("circle");
    expect(preview).toHaveAttribute("r", "24");
  });

  it("uses erase strokes to cut holes from the visible mask instead of painting white", () => {
    render(
      <div style={{ height: 300, position: "relative", width: 300 }}>
        <LocalMaskLayer
          brushPreview={null}
          height={300}
          strokes={[
            { id: "paint", mode: "brush", points: [{ x: 0.5, y: 0.5 }], radius: 80 },
            { id: "erase", mode: "erase", points: [{ x: 0.5, y: 0.5 }], radius: 32 },
          ]}
          width={300}
        />
      </div>,
    );

    expect(screen.getByTestId("local-mask-fill")).toHaveAttribute("mask", expect.stringContaining("url(#"));
    const eraseStroke = screen.getByTestId("local-mask-stroke-erase");
    expect(eraseStroke).toHaveAttribute("fill", "black");
    expect(eraseStroke).not.toHaveAttribute("fill", expect.stringContaining("255,255,255"));
  });
});
