import "../../i18n";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ImageCard, type ImageCardProps } from "./ImageCard";
import type { Revision } from "./api";

afterEach(cleanup);
const revision = (over: Partial<Revision> = {}): Revision => ({ id: "r", branch_id: "b", parent_revision_id: null, asset_id: "a", root_source_asset_id: "a",
  root_image_url: "/a.png", image_url: "/a.png", operation_id: null, source_kind: "ai", view: "front", created_at: "", recipe: null, number: 3, can_continue: true, can_regenerate: true,
  width: 300, height: 400, ...over });
function mount(over: Partial<Revision> = {}, props: Partial<ImageCardProps> = {}) {
  const handlers = { onOpen: vi.fn(), onRegenerate: vi.fn(), onEdit: vi.fn(), onViewsets: vi.fn(), onMakeHeadShell: vi.fn() };
  render(<ImageCard revision={revision(over)} canRegenerate canEdit canViewsets canMakeHeadShell {...handlers} {...props} />);
  return handlers;
}

it("stamps a head-shell image with its kind and version, and offers the three first-level actions", () => {
  mount();
  expect(screen.getByText("头壳 v3")).toBeVisible();
  expect(screen.getByRole("img", { name: "头壳 v3" })).toBeInTheDocument();
  for (const name of ["重新生成", "进入编辑", "生成四视图"]) expect(screen.getByRole("button", { name })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "用它生成头壳" })).not.toBeInTheDocument();
});

it("treats a version without a declared kind as a head shell", () => {
  mount({ image_kind: undefined });
  expect(screen.getByText("头壳 v3")).toBeVisible();
});

it("makes a reference image primarily lead to a head shell and blocks four views", () => {
  const handlers = mount({ image_kind: "reference", number: 2 });
  expect(screen.getByText("参考图 v2")).toBeVisible();
  expect(screen.getByRole("img", { name: "参考图 v2" })).toBeInTheDocument();
  const main = screen.getByRole("button", { name: "用它生成头壳" });
  expect(main).toHaveClass("v2-primary");
  fireEvent.click(main);
  expect(handlers.onMakeHeadShell).toHaveBeenCalledTimes(1);
  const views = screen.getByRole("button", { name: "生成四视图" });
  expect(views).toBeDisabled();
  expect(views).toHaveAttribute("title", expect.stringContaining("先生成头壳"));
  fireEvent.click(views);
  expect(handlers.onViewsets).not.toHaveBeenCalled();
});

it("keeps the shapes the image actions rely on: aspect ratio, zoom and disabled states", () => {
  const handlers = mount({}, { canRegenerate: false, canEdit: false });
  expect(screen.getByRole("button", { name: "重新生成" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "进入编辑" })).toBeDisabled();
  const open = screen.getByRole("button", { name: /查看图片/ });
  expect(open.style.aspectRatio).toBe("0.75 / 1");
  fireEvent.click(open);
  expect(handlers.onOpen).toHaveBeenCalledTimes(1);
});

it("does not show a version number it does not have", () => {
  mount({ number: undefined });
  expect(screen.getByRole("img", { name: "头壳设计" })).toBeInTheDocument();
});
