import "../../../i18n";
import { forwardRef } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const list = vi.hoisted(() => vi.fn());
vi.mock("./repository", () => ({
  editorDrafts: { list, save: vi.fn(), remove: vi.fn() },
  inspectServedSource: vi.fn(),
}));
vi.mock("../EditorWorkspace", () => ({
  EditorWorkspace: forwardRef((props: { initialDraft?: { localEditNote?: string } }, _ref) =>
    <div data-testid="workspace" data-note={props.initialDraft?.localEditNote ?? ""} />),
}));
import { PersistentEditor } from "./PersistentEditor";

const source = { blob: new Blob(["x"]), sha256: "a".repeat(64), width: 10, height: 10 };
const record = (id: string, updatedAt: number, note: string) => ({
  id, version: 1, updatedAt, schemaVersion: 2, source,
  content: { recipe: undefined, maskStrokes: [], localEditNote: note, selectedLocalReferenceKeys: [] },
});
beforeEach(() => { URL.createObjectURL = vi.fn(() => "blob:x"); URL.revokeObjectURL = vi.fn(); });
afterEach(() => { cleanup(); list.mockReset(); });

it("opens the newest saved copy without asking which of several to use", async () => {
  list.mockResolvedValue([record("new", 200, "newest"), record("old", 100, "older")]);
  render(<PersistentEditor scope={{ ownerId: "u", branchId: "b", baseRevisionId: "r", rootAssetId: "a" }} sourceUrl="/x" />);
  const workspace = await screen.findByTestId("workspace");
  expect(workspace).toHaveAttribute("data-note", "newest");
  expect(screen.queryByText(/多个草稿|multiple drafts/i)).toBeNull();
  await waitFor(() => expect(screen.queryByRole("button")).toBeNull());
});
