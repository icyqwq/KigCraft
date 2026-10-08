import "../../i18n";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Lingering } from "./Lingering";
import { WorkspaceHeader } from "./WorkspaceHeader";
import { handOverSharedElement, markTransition, sharedName } from "./transitions";

beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); document.documentElement.removeAttribute("data-vt-kind"); });

function Overlay({ open }: { open: boolean }) {
  return <Lingering when={open} render={closing => <div data-testid="overlay" data-closing={String(closing)}>panel</div>} />;
}

it("plays the exit before an overlay removed from the address unmounts", () => {
  const view = render(<Overlay open />);
  expect(screen.getByTestId("overlay")).toHaveAttribute("data-closing", "false");
  view.rerender(<Overlay open={false} />);
  // Still there, now told it is closing, so it can run its exit animation.
  expect(screen.getByTestId("overlay")).toHaveAttribute("data-closing", "true");
  act(() => { vi.advanceTimersByTime(239); });
  expect(screen.getByTestId("overlay")).toBeInTheDocument();
  act(() => { vi.advanceTimersByTime(2); });
  expect(screen.queryByTestId("overlay")).not.toBeInTheDocument();
});

it("shows the overlay again immediately when the address brings it back mid-exit", () => {
  const view = render(<Overlay open />);
  view.rerender(<Overlay open={false} />);
  act(() => { vi.advanceTimersByTime(100); });
  view.rerender(<Overlay open />);
  expect(screen.getByTestId("overlay")).toHaveAttribute("data-closing", "false");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(screen.getByTestId("overlay")).toBeInTheDocument();
});

it("renders nothing for an overlay that was never opened", () => {
  render(<Overlay open={false} />);
  expect(screen.queryByTestId("overlay")).not.toBeInTheDocument();
});

it("exits within 120 ms under reduced motion", () => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce"), media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false }));
  try {
    const view = render(<Overlay open />);
    view.rerender(<Overlay open={false} />);
    act(() => { vi.advanceTimersByTime(121); });
    expect(screen.queryByTestId("overlay")).not.toBeInTheDocument();
  } finally { vi.unstubAllGlobals(); }
});

it("puts back, title, secondary and the main action in that order", () => {
  const onBack = vi.fn();
  render(<WorkspaceHeader title="编辑 v3" backLabel="返回对话" onBack={onBack} secondary={<button>导出</button>} primary={<button className="v2-primary">保存并返回</button>} />);
  const names = [...document.querySelector("header")!.children].map(node => node.textContent);
  expect(names).toEqual(["返回对话", "编辑 v3", "导出", "保存并返回"]);
  fireEvent.click(screen.getByRole("button", { name: "返回对话" }));
  expect(onBack).toHaveBeenCalledTimes(1);
  expect(document.querySelector("[data-testid=header-language-button]")).toBeNull();
});

it("hands the shared element name from the card image to the lightbox image, one holder at a time", () => {
  const card = document.createElement("img"), box = document.createElement("img");
  card.dataset.vtSource = "r1"; card.style.viewTransitionName = sharedName("r1");
  document.body.append(card, box);
  const release = handOverSharedElement("r1", box);
  expect(card.style.viewTransitionName).toBe("");
  expect(box.style.viewTransitionName).toBe("revision-r1");
  release();
  expect(box.style.viewTransitionName).toBe("");
  card.remove(); box.remove();
});

it("marks the kind of transition for the stylesheet and clears it afterwards", () => {
  markTransition("design");
  expect(document.documentElement.dataset.vtKind).toBe("design");
  act(() => { vi.advanceTimersByTime(900); });
  expect(document.documentElement.dataset.vtKind).toBeUndefined();
});
