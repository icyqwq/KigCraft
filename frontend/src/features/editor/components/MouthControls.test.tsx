import { ThemeProvider } from "@mui/material/styles";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../../app/theme";
import { createEmptyRecipe } from "../deformation/recipe";
import { MouthControls } from "./MouthControls";

describe("MouthControls", () => {
  afterEach(() => {
    cleanup();
  });

  it("adjusts the vertical mouth position in hundredths", () => {
    const onChange = vi.fn();
    const recipe = createEmptyRecipe();

    render(
      <ThemeProvider theme={kigTheme}>
        <MouthControls
          values={recipe.mouth}
          onChange={onChange}
          onReset={vi.fn()}
        />
      </ThemeProvider>,
    );

    const slider = screen.getByRole("slider", { name: "上下位置" });
    expect(slider).toHaveAttribute("step", "0.01");
    expect(screen.getByText("0.00")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "上下位置增加" }));

    expect(onChange).toHaveBeenCalledWith("mouthVertical", 0.0006);
  });
});
