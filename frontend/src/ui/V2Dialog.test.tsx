import i18n from "../i18n";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { V2Dialog } from "./V2Dialog";
import "./Toast";
import { toast } from "./toastStore";

afterEach(cleanup);
function Harness({ onClose = () => {}, dismissible = true }: { onClose?: () => void; dismissible?: boolean }) {
  const [open, setOpen] = useState(false);
  return <>
    <button onClick={() => setOpen(true)}>open</button>
    <V2Dialog open={open} title="重命名" dismissible={dismissible} onClose={() => { onClose(); setOpen(false); }}
      actions={<><button>取消</button><button className="v2-primary">保存</button></>}>
      <input aria-label="name" />
    </V2Dialog>
  </>;
}
it("puts the title left and × right, focuses the first field, and returns focus on close", async () => {
  render(<Harness />);
  const trigger = screen.getByText("open");
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: "重命名" });
  await waitFor(() => expect(screen.getByLabelText("name")).toHaveFocus());
  const head = dialog.querySelector(".v2-dialog-head")!;
  expect(head.firstElementChild?.tagName).toBe("H2");
  expect(head.lastElementChild).toBe(screen.getByRole("button", { name: i18n.t("common.close") }));
  const actions = [...dialog.querySelectorAll(".v2-actions button")].map(button => button.textContent);
  expect(actions).toEqual(["取消", "保存"]);
  expect(dialog.querySelector(".MuiSelect-root,[data-testid=header-language-button]")).toBeNull();
  fireEvent.keyDown(dialog, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(trigger).toHaveFocus();
});
it("closes from the × button and the scrim, but not while dismissal is disabled", async () => {
  const onClose = vi.fn();
  const view = render(<Harness onClose={onClose} />);
  fireEvent.click(screen.getByText("open"));
  fireEvent.click(await screen.findByRole("button", { name: i18n.t("common.close") }));
  expect(onClose).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  view.unmount();
  const blocked = vi.fn();
  render(<Harness onClose={blocked} dismissible={false} />);
  fireEvent.click(screen.getByText("open"));
  const dialog = await screen.findByRole("dialog");
  fireEvent.keyDown(dialog, { key: "Escape" });
  fireEvent.click(document.querySelector(".MuiBackdrop-root")!);
  expect(screen.getByRole("button", { name: i18n.t("common.close") })).toBeDisabled();
  expect(blocked).not.toHaveBeenCalled();
});
it("plays the exit transition before unmounting", async () => {
  render(<Harness />);
  fireEvent.click(screen.getByText("open"));
  const dialog = await screen.findByRole("dialog");
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(document.querySelector(".v2-dialog.is-closing")).not.toBeNull();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});
it("shows a toast as a status message and removes it after two seconds", () => {
  vi.useFakeTimers();
  try {
    act(() => toast("已保存"));
    expect(screen.getByRole("status")).toHaveTextContent("已保存");
    act(() => { vi.advanceTimersByTime(1999); });
    expect(screen.queryByRole("status")).not.toBeNull();
    act(() => { vi.advanceTimersByTime(2); });
    expect(screen.queryByRole("status")).toBeNull();
  } finally { vi.useRealTimers(); }
});
