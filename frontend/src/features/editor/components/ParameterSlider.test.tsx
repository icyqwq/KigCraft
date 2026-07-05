import { ThemeProvider } from "@mui/material/styles";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../../app/theme";
import { ParameterSlider } from "./ParameterSlider";

function renderParameterSlider(props: Partial<React.ComponentProps<typeof ParameterSlider>> = {}) {
  const onChange = props.onChange ?? vi.fn();
  const onReset = props.onReset ?? vi.fn();

  render(
    <ThemeProvider theme={kigTheme}>
      <ParameterSlider
        dataTestId="parameter-slider"
        label="娴嬭瘯"
        max={props.max ?? 4}
        min={props.min ?? -2}
        step={props.step ?? 0.1}
        value={props.value ?? 0}
        onChange={onChange}
        onReset={onReset}
        {...props}
      />
    </ThemeProvider>,
  );

  return {
    onChange,
    slider: screen.getByTestId("parameter-slider").querySelector('input[type="range"]') as HTMLElement,
  };
}

describe("ParameterSlider", () => {
  afterEach(() => {
    cleanup();
  });

  it("uses the full real range for asymmetric bidirectional ranges", () => {
    const { slider } = renderParameterSlider({ max: 4, min: -2, value: 0 });

    expect(slider).toHaveAttribute("aria-valuemin", "-2");
    expect(slider).toHaveAttribute("aria-valuemax", "4");
    expect(slider).toHaveAttribute("aria-valuenow", "0");
  });

  it("starts positive-only controls at the left edge and prevents negative movement", () => {
    const { onChange, slider } = renderParameterSlider({ max: 5, min: 0, value: 0 });

    expect(slider).toHaveAttribute("aria-valuemin", "0");
    expect(slider).toHaveAttribute("aria-valuemax", "5");
    expect(slider).toHaveAttribute("aria-valuenow", "0");
    expect(slider).toHaveStyle({ "--parameter-slider-progress": "0%" });

    slider.focus();
    fireEvent.keyDown(slider, { key: "ArrowLeft" });

    expect(onChange).not.toHaveBeenCalledWith(expect.any(Number));
    expect(slider).toHaveAttribute("aria-valuenow", "0");
  });

  it("formats debug values from the current visual slider value", () => {
    renderParameterSlider({
      debugValue: "1.00",
      debugValueFormatter: (value) => (value * 0.2).toFixed(3),
      max: 1,
      min: -1,
      precision: 2,
      step: 0.01,
      value: 1,
    });

    expect(screen.getByText("1.00 (0.200)")).toBeInTheDocument();
  });
});

