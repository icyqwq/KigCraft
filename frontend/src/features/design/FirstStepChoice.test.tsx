import "../../i18n";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FirstStepChoice } from "./FirstStepChoice";
import { Composer } from "./Composer";

afterEach(cleanup);

it("offers the two first steps, flat reference preselected, with no explanatory text", () => {
  const onChange = vi.fn();
  render(<FirstStepChoice value="reference" onChange={onChange} />);
  expect(screen.getByRole("radio", { name: "先生成平面参考图（推荐）" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "直接生成头壳" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("radio", { name: "直接生成头壳" }));
  expect(onChange).toHaveBeenCalledWith("head_shell");
  expect(document.body.textContent).not.toMatch(/去掉背景|Strips background/);
});

it("lives outside the message box, not inside it", () => {
  const { container } = render(<><FirstStepChoice value="reference" onChange={() => {}} />
    <Composer canGenerate busy={false} requireAuth={action => { void action(); }} onSubmitted={async () => {}} /></>);
  const box = container.querySelector(".v2-composer")!;
  expect(box.contains(screen.getByRole("radiogroup"))).toBe(false);
  expect(screen.getByRole("radiogroup").compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
