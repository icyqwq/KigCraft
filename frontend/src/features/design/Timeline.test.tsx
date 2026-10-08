import "../../i18n";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { designApi, type Revision, type Snapshot } from "./api";
import { historyTimeline } from "./historyTimeline";
import { Timeline, type TimelineProps } from "./Timeline";
import zh from "./page-copy-zh-CN";

// These tests mount a full composer; give async queries room when the whole suite runs in parallel.
configure({ asyncUtilTimeout: 5000 });
beforeEach(() => {
  sessionStorage.clear();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const revision = (over: Partial<Revision> = {}): Revision => ({ id: "rev-1", branch_id: "branch", parent_revision_id: null, asset_id: "a", root_source_asset_id: "a",
  root_image_url: "/a.png", image_url: "/a.png", operation_id: null, source_kind: "ai", view: "front", created_at: "2026-01-01T00:00:00Z", recipe: null,
  number: 1, history_sequence: 2, can_continue: true, can_regenerate: true, ...over });
const message = (id: string, role: string, text: string, sequence: number, can_edit = false) => ({ id, role, status: "completed", created_at: "2026-01-01T00:00:0" + sequence + "Z",
  operation_id: "op", history_sequence: sequence, branch_id: "branch", can_edit, content_parts: [{ type: "text", text }] });
const snapshot = (over: Partial<Snapshot> = {}): Snapshot => ({ id: "design", title: "Coral", last_branch_id: "branch", pinned: false, metadata_version: 0, updated_at: "",
  snapshot_sequence: 5, branch: { id: "branch", state_version: 3, current_revision_id: "rev-1" },
  messages: [message("m1", "user", "做一个珊瑚色头壳", 1, true)], revisions: [revision()], operations: [], ...over } as Snapshot);

function mount(over: Partial<TimelineProps> = {}, data = snapshot()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props: TimelineProps = { ownerId: "user", data, snapshot: data, items: historyTimeline(undefined, data), capabilities: { history_fork: true, viewsets: true, new_operations: true, local_edits: false, max_input_images: 8 },
    canGenerate: true, snapshotFailed: false, editingId: null, onStartEdit: vi.fn(), onStopEdit: vi.fn(), onStream: vi.fn(), onOpenViewer: vi.fn(), onRegenerate: vi.fn(),
    onEditImage: vi.fn(), onCreateViewset: vi.fn(), onMakeHeadShell: vi.fn(), onOpenViewset: vi.fn(), onSubmitted: vi.fn().mockResolvedValue(undefined), onSignIn: vi.fn(), ...over };
  const view = render(<QueryClientProvider client={client}><MemoryRouter><Timeline {...props} /></MemoryRouter></QueryClientProvider>);
  return { ...view, props };
}
const preparation = { message_id: "m1", source_revision_id: null, branch_version: 3, text: "做一个珊瑚色头壳", base_image_url: null, references: [], reserved_images: 0, local_edit_mode: null, annotations: [] };

it("edits a message in place and follows the branch the server returns, replacing the address", async () => {
  vi.spyOn(designApi, "prepareMessageEdit").mockResolvedValue(preparation as any);
  vi.spyOn(designApi, "quote").mockResolvedValue({ id: "q", max_units: 1, expires_at: "" });
  const submit = vi.spyOn(designApi, "submit").mockResolvedValue({ id: "op2", branch_id: "forked", kind: "generate", state: "queued", base_revision_id: null, reserved_units: 1, settled_units: 0, error_code: null });
  const first = mount();
  fireEvent.click(screen.getByRole("button", { name: "修改后重发" }));
  expect(first.props.onStartEdit).toHaveBeenCalledWith("m1");
  first.unmount();
  const editing = mount({ editingId: "m1" });
  const group = await screen.findByRole("group", { name: "修改后重发" });
  // No dialog and no plan wording: the bubble itself became the editor.
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/方案|另建/);
  const box = await within(group).findByRole("textbox", { name: "设计要求" });
  await waitFor(() => expect(box).toHaveValue("做一个珊瑚色头壳"));
  fireEvent.change(box, { target: { value: "改成蓝色" } });
  fireEvent.click(within(group).getByRole("button", { name: "发送", exact: true }));
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(editing.props.onSubmitted).toHaveBeenCalledWith("design", "forked", true));
  expect(editing.props.onStopEdit).toHaveBeenCalled();
});

it("cancels an in-place edit without sending", async () => {
  vi.spyOn(designApi, "prepareMessageEdit").mockResolvedValue(preparation as any);
  const submit = vi.spyOn(designApi, "submit");
  const view = mount({ editingId: "m1" });
  const group = await screen.findByRole("group", { name: "修改后重发" });
  fireEvent.click(await within(group).findByRole("button", { name: "取消" }));
  expect(view.props.onStopEdit).toHaveBeenCalledTimes(1);
  expect(submit).not.toHaveBeenCalled();
});

it("answers a clarification under the question, with no dialog", async () => {
  const question = { operation_id: "ask", version: 1, state: "open" as const, expires_at: "2999-01-01T00:00:00Z", messages: [{ message_id: "m2", text: "要什么颜色？" }], answered_by: null };
  const data = snapshot({ messages: [message("m1", "user", "做头壳", 1, true), message("m2", "assistant", "要什么颜色？", 2)], revisions: [], questions: [question] });
  vi.spyOn(designApi, "prepareQuestion").mockResolvedValue({ ...preparation, message_id: "m2", ...question, branch_id: "branch" } as any);
  vi.spyOn(designApi, "quote").mockResolvedValue({ id: "q", max_units: 1, expires_at: "" });
  const submit = vi.spyOn(designApi, "submit").mockResolvedValue({ id: "op3", branch_id: "branch", kind: "generate", state: "queued", base_revision_id: null, reserved_units: 1, settled_units: 0, error_code: null });
  const view = mount({}, data);
  const answer = await screen.findByRole("group", { name: "补充回答" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /回答/ })).not.toBeInTheDocument();
  const box = await within(answer).findByRole("textbox", { name: "补充回答" });
  fireEvent.change(box, { target: { value: "珊瑚红" } });
  fireEvent.click(within(answer).getByRole("button", { name: "发送", exact: true }));
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
  expect(submit.mock.calls[0][1]).toEqual(expect.objectContaining({ answer_to: { operation_id: "ask", version: 1 } }));
  await waitFor(() => expect(view.props.onSubmitted).toHaveBeenCalledWith("design", "branch"));
});

it("does not read or show an answer box for answered and expired questions", () => {
  const prepare = vi.spyOn(designApi, "prepareQuestion");
  const answered = { operation_id: "a", version: 1, state: "answered" as const, expires_at: "", messages: [{ message_id: "m2", text: "?" }], answered_by: "x" };
  const data = snapshot({ messages: [message("m2", "assistant", "要什么颜色？", 2)], revisions: [], questions: [answered] });
  mount({}, data);
  expect(screen.getByRole("status")).toHaveTextContent(zh.questionAnswered);
  expect(prepare).not.toHaveBeenCalled();
});

it("reserves the picture at the base ratio while a task runs and shows the real stage", () => {
  const wide = revision({ width: 400, height: 200 });
  const data = snapshot({ revisions: [wide], operations: [{ id: "op", branch_id: "branch", kind: "generate", state: "running", base_revision_id: "rev-1", reserved_units: 1, settled_units: 0, error_code: null }] });
  const view = mount({ active: data.operations[0] }, data);
  const frame = view.container.querySelector(".v2-placeholder-paper") as HTMLElement;
  expect(frame.style.aspectRatio).toBe("2 / 1");
  expect(view.container.querySelector("img.kc-logo-loader[src=\"/brand/kigcraft-loader.svg\"]")).not.toBeNull();
  expect(screen.getByText(zh.running)).toBeVisible();
  // The old text stop key is gone from the timeline; stopping lives on the send key.
  expect(screen.queryByRole("button", { name: "停止" })).not.toBeInTheDocument();
});

it("falls back to a square placeholder when the base has no size", () => {
  const data = snapshot({ revisions: [], current_revision: null, operations: [{ id: "op", branch_id: "branch", kind: "generate", state: "queued", base_revision_id: null, reserved_units: 1, settled_units: 0, error_code: null }] });
  const view = mount({ active: data.operations[0] }, data);
  expect((view.container.querySelector(".v2-placeholder-paper") as HTMLElement).style.aspectRatio).toBe("1 / 1");
});

it("animates only images that arrive after the page was opened", () => {
  const first = snapshot();
  const view = mount({}, first);
  expect(view.container.querySelector(".v2-image-card.is-new")).toBeNull();
  const second = snapshot({ revisions: [revision(), revision({ id: "rev-2", number: 2, history_sequence: 3 })] });
  view.rerender(<QueryClientProvider client={new QueryClient()}><MemoryRouter><Timeline {...view.props} data={second} snapshot={second} items={historyTimeline(undefined, second)} /></MemoryRouter></QueryClientProvider>);
  expect(view.container.querySelectorAll(".v2-image-card.is-new")).toHaveLength(1);
});

it("keeps the first-step choice when the first message is edited and resent", async () => {
  vi.spyOn(designApi, "prepareMessageEdit").mockResolvedValue({ ...preparation, target_kind: "reference" } as any);
  const quote = vi.spyOn(designApi, "quote").mockResolvedValue({ id: "q", max_units: 1, expires_at: "" });
  vi.spyOn(designApi, "submit").mockResolvedValue({ id: "op2", branch_id: "forked", kind: "generate", state: "queued", base_revision_id: null, reserved_units: 1, settled_units: 0, error_code: null });
  mount({ editingId: "m1" });
  const group = await screen.findByRole("group", { name: "修改后重发" });
  const box = await within(group).findByRole("textbox", { name: "设计要求" });
  await waitFor(() => expect(box).toHaveValue("做一个珊瑚色头壳"));
  fireEvent.click(within(group).getByRole("button", { name: "发送", exact: true }));
  await waitFor(() => expect(quote).toHaveBeenCalled());
  expect(quote.mock.calls[0][1]).toEqual(expect.objectContaining({ target_kind: "reference" }));
});
