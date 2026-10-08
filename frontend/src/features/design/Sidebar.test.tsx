import "../../i18n";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Sidebar, type SidebarProps } from "./Sidebar";
import { EmptyConversation, DesignMissing } from "./EmptyStates";
import { designApi } from "./api";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
beforeEach(() => { vi.useRealTimers(); });
const now = new Date();
const daysAgo = (days: number) => new Date(now.getTime() - days * 86400000 - 60000).toISOString();
const design = (id: string, title: string, over: Record<string, unknown> = {}) => ({ id, title, last_branch_id: "b", pinned: false, metadata_version: 1,
  updated_at: now.toISOString(), last_activity_at: now.toISOString(), ...over });

function mount(items: any[], props: Partial<SidebarProps> = {}) {
  vi.spyOn(designApi, "list").mockResolvedValue({ items, next_cursor: null } as any);
  const handlers = { onNavigate: vi.fn(), onNew: vi.fn(), onRename: vi.fn(), onRemoved: vi.fn().mockResolvedValue(undefined), onClose: vi.fn() };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><MemoryRouter>
    <Sidebar userId="user" enabled selectedId="a" open={false} account={{ user: { id: "user", email: "ada@example.com" }, epoch: 0, onLogin: vi.fn(), onOpenAccount: vi.fn(), onLogout: vi.fn() }}
      {...handlers} {...props} /></MemoryRouter></QueryClientProvider>);
  return { ...view, handlers };
}

it("groups designs by pinned, today, yesterday, last 7 days and earlier, and renders each as a real link", async () => {
  mount([design("a", "今天的设计"), design("p", "置顶的", { pinned: true }), design("y", "昨天的", { last_activity_at: daysAgo(1) }),
    design("w", "三天前", { last_activity_at: daysAgo(3) }), design("o", "很久以前", { last_activity_at: daysAgo(30) })]);
  await screen.findByRole("link", { name: /今天的设计/ });
  const headings = screen.getAllByRole("heading", { level: 2 }).map(item => item.textContent);
  expect(headings).toEqual(["置顶", "今天", "昨天", "7 天内", "更早"]);
  const link = screen.getByRole("link", { name: /三天前/ });
  expect(link).toHaveAttribute("href", "/v2/w");
  expect(screen.getByRole("link", { name: /今天的设计/ })).toHaveAttribute("aria-current", "page");
  expect(screen.queryByRole("button", { name: "今天的设计" })).not.toBeInTheDocument();
});

it("shows state as visuals: generating, needs answer and draft", async () => {
  mount([design("g", "生成中", { operation_state: "running" }), design("q", "待回答", { operation_state: "awaiting_input" }), design("d", "有草稿", { has_cloud_draft: true })]);
  await screen.findByRole("link", { name: /生成中/ });
  expect(within(screen.getByRole("link", { name: /生成中/ })).getByRole("img", { name: "正在生成" })).toBeInTheDocument();
  expect(within(screen.getByRole("link", { name: /待回答/ })).getByRole("img", { name: "需要回答" })).toBeInTheDocument();
  expect(within(screen.getByRole("link", { name: /有草稿/ })).getByRole("img", { name: "有草稿" })).toBeInTheDocument();
});

it("offers rename, pin and delete (archiving hidden) from a menu on each item, and blocks delete while generating", async () => {
  const { handlers } = mount([design("a", "今天的设计"), design("g", "生成中", { operation_state: "running" })]);
  await screen.findByRole("link", { name: /今天的设计/ });
  fireEvent.click(screen.getByRole("button", { name: /更多操作 · 今天的设计/ }));
  expect(screen.getByRole("menuitem", { name: "重命名" })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "置顶" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "归档" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("menuitem", { name: "重命名" }));
  expect(handlers.onRename).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }));
  fireEvent.click(screen.getByRole("button", { name: /更多操作 · 生成中/ }));
  expect(screen.getByRole("menuitem", { name: "永久删除" })).toHaveAttribute("aria-disabled", "true");
});

it("hides the archive entry, has no trash, and still lets an archived design be restored", async () => {
  mount([design("a", "归档里的设计", { archived_at: "2026-01-01T00:00:00Z" })]);
  await screen.findByRole("link", { name: /归档里的设计/ });
  expect(screen.queryByRole("button", { name: "回收站" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "已归档" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /更多操作 · 归档里的设计/ }));
  expect(screen.getByRole("menuitem", { name: "恢复设计" })).toBeInTheDocument();
});

it("has distinct empty states for no designs and for no search results with a clear-search key", async () => {
  mount([]);
  expect(await screen.findByText("还没有设计")).toBeVisible();
  cleanup();
  mount([]);
  fireEvent.change(await screen.findByRole("textbox", { name: "搜索设计" }), { target: { value: "不存在" } });
  expect(await screen.findByText("没有找到相关设计")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
  expect(screen.getByRole("textbox", { name: "搜索设计" })).toHaveValue("");
});

it("shows a skeleton, not a loading sentence, while the list loads", () => {
  vi.spyOn(designApi, "list").mockReturnValue(new Promise(() => {}));
  const client = new QueryClient();
  const { container } = render(<QueryClientProvider client={client}><MemoryRouter>
    <Sidebar userId="user" enabled open={false} account={{ user: null, epoch: 0, onLogin: vi.fn(), onOpenAccount: vi.fn(), onLogout: vi.fn() }}
      onNavigate={vi.fn()} onNew={vi.fn()} onRename={vi.fn()} onRemoved={vi.fn()} onClose={vi.fn()} /></MemoryRouter></QueryClientProvider>);
  expect(container.querySelector(".v2-skeleton-row")).not.toBeNull();
  expect(screen.queryByText(/正在读取|正在加载/)).not.toBeInTheDocument();
});

it("puts square initial, email, quota and an account menu with a language submenu at the foot", async () => {
  vi.spyOn(designApi, "quota").mockResolvedValue({ available: 12, reserved: 0, balance: 12, status: "ok", entries: [] } as any);
  const onOpenAccount = vi.fn(), onLogout = vi.fn();
  mount([], { account: { user: { id: "user", email: "ada@example.com" }, epoch: 0, onLogin: vi.fn(), onOpenAccount, onLogout } });
  const button = await screen.findByRole("button", { name: /账户菜单 · ada@example.com/ });
  expect(button).toHaveTextContent("A");
  await waitFor(() => expect(button).toHaveTextContent("12"));
  fireEvent.click(button);
  expect(screen.getByRole("menuitem", { name: "账户与额度" })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "关于" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("menuitem", { name: "语言" }));
  expect(await screen.findByRole("menuitem", { name: "English" })).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole("menuitem", { name: "English" })); });
  const { default: i18n } = await import("../../i18n");
  expect(i18n.language.startsWith("en")).toBe(true);
  await act(async () => { await i18n.changeLanguage("zh-CN"); });
  fireEvent.click(screen.getByRole("button", { name: /账户菜单/ }));
  fireEvent.click(screen.getByRole("menuitem", { name: "退出登录" }));
  expect(onLogout).toHaveBeenCalledTimes(1);
});

it("starts an empty conversation with the first-step choice and a missing design with a way back", () => {
  
  render(<MemoryRouter><EmptyConversation /></MemoryRouter>);
  // The old "upload a character image" and "describe an original character" starters are gone.
  expect(screen.queryByRole("button", { name: /上传角色图|描述一个原创角色/ })).not.toBeInTheDocument();
  // Local editing moved to the top bar and the first-step choice into the message box.
  expect(screen.queryByRole("link", { name: "编辑本地图片" })).not.toBeInTheDocument();
  expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  cleanup();
  render(<MemoryRouter><DesignMissing /></MemoryRouter>);
  expect(screen.getByRole("alert")).toHaveTextContent("这个设计不存在");
  expect(screen.getByRole("link", { name: "回到设计列表" })).toHaveAttribute("href", "/v2");
});
