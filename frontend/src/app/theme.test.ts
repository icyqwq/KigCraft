import { describe, expect, it } from "vitest";
import { kigTheme } from "./theme";

describe("kigTheme", () => {
  it("uses the print workbench styling with red-orange accent", () => {
    expect(kigTheme.palette.mode).toBe("light");
    expect(kigTheme.palette.primary.main).toBe("#c9552f");
    expect(kigTheme.palette.background.default).toBe("#f3ead7");
  });

  it("sets hard-edged Material UI defaults", () => {
    expect(kigTheme.shape.borderRadius).toBe(0);
    expect(kigTheme.components?.MuiButton?.defaultProps).toMatchObject({
      disableElevation: true,
    });
  });
});

