import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { LogoLoader } from "./LogoLoader";
import { CircularProgress } from "./mui";

afterEach(cleanup);

it("shows the KigCraft loading mark and stays silent when the surrounding text names the state", () => {
  const { container } = render(<div role="status">正在生成<LogoLoader size={28} /></div>);
  const image = container.querySelector("img")!;
  expect(image).toHaveAttribute("src", "/brand/kigcraft-loader.svg");
  expect(image).toHaveAttribute("width", "28");
  expect(image).toHaveAttribute("aria-hidden", "true");
  expect(screen.getByRole("status")).toHaveTextContent("正在生成");
});

it("is announced as a progress indicator when given a label", () => {
  render(<LogoLoader label="加载中" />);
  expect(screen.getByRole("progressbar", { name: "加载中" })).toHaveAttribute("src", "/brand/kigcraft-loader.svg");
});

it("keeps the mark for image generation and gives small inline waits a plain spinner", () => {
  const { container } = render(<CircularProgress size={18} />);
  expect(container.querySelector("img.kc-logo-loader")).toBeNull();
  expect(container.querySelector(".kc-spinner")).not.toBeNull();
  expect(screen.getByRole("progressbar")).toBeInTheDocument();
});
