import { publicUrl } from "../../ui/publicUrl";
import { useDeferredValue, useMemo, useState } from "react";
import {ErrorToast} from "../../ui/ErrorToast";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { LayoutGroup, motion, useReducedMotion } from "motion/react";
import { IconArrowLeft, IconBrandGithub, IconMessageCircleQuestion, IconPencil, IconPin, IconPlus, IconSearch, IconSparkles } from "@tabler/icons-react";
import { activeStates, designApi, errorText, type Design } from "./api";
import { AccountMenu, type AccountMenuProps } from "./AccountMenu";
import { DesignActions } from "./DesignActions";
import { SidebarSkeleton } from "./Skeletons";
import { GITHUB_URL } from "../../app/links";
import { useDesignCopy, type DesignCopyKey } from "./useDesignCopy";

type ListStatus = "active" | "archived";
type Item = Design & {
  thumbnail_url?: string | null; operation_state?: string | null; has_cloud_draft?: boolean;
  match?: { branch_id: string; message_id: string; text: string } | null;
};
const dayStart = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
const DAY = 24 * 60 * 60 * 1000;
/** Pinned first, then by calendar distance from today. */
function groupOf(item: Item, now: number): DesignCopyKey {
  if (item.pinned) return "groupPinned";
  const stamp = item.last_activity_at ?? item.updated_at;
  const age = (dayStart(new Date(now)) - dayStart(new Date(stamp))) / DAY;
  return age <= 0 ? "groupToday" : age <= 1 ? "groupYesterday" : age <= 7 ? "groupWeek" : "groupEarlier";
}
const groupOrder: DesignCopyKey[] = ["groupPinned", "groupToday", "groupYesterday", "groupWeek", "groupEarlier"];

export type SidebarProps = {
  userId?: string; enabled: boolean; selectedId?: string; open: boolean;
  account: AccountMenuProps;
  /** Close the mobile drawer and reset page-level transient state. */
  onNavigate: () => void;
  onNew: () => void;
  onRename: (design: Design) => void;
  /** The design is already deleted; the page leaves it when it was the open one. */
  onRemoved: (design: Design) => Promise<void>;
  onClose: () => void;
};

export function Sidebar(props: SidebarProps) {
  const { userId, enabled, selectedId } = props;
  const { c, language } = useDesignCopy();
  const client = useQueryClient();
  const reduced = useReducedMotion();
  const [search, setSearch] = useState("");
  const query = useDeferredValue(search);
  const [view, setView] = useState<ListStatus>("active");
  const designs = useInfiniteQuery({ queryKey: ["v2", userId, "designs", query, view],
    queryFn: ({ pageParam, signal }) => designApi.list(query, view, pageParam, signal),
    initialPageParam: null as string | null, getNextPageParam: page => page.next_cursor ?? undefined,
    enabled: enabled && Boolean(userId), retry: false });
  const items = useMemo<Item[]>(() => [...new Map((designs.data?.pages.flatMap(page => page.items) ?? []).map(item => [item.id, item as Item])).values()], [designs.data]);
  const refresh = async () => { await client.invalidateQueries({ queryKey: ["v2"] }); };
  const groups = useMemo(() => {
    const now = Date.now(), buckets = new Map<DesignCopyKey, Item[]>();
    for (const item of items) { const key = groupOf(item, now); buckets.set(key, [...(buckets.get(key) ?? []), item]); }
    return groupOrder.flatMap(key => buckets.has(key) ? [{ key, items: buckets.get(key)! }] : []);
  }, [items]);
  const searching = query.trim().length > 0;
  const time = (stamp?: string) => stamp ? new Date(stamp).toLocaleDateString(language, { month: "short", day: "numeric" }) : "";

  function row(design: Item) {
    const selected = design.id === selectedId;
    const generating = design.operation_state && activeStates.has(design.operation_state);
    const needsAnswer = design.operation_state === "awaiting_input";
    const to = "/v2/" + design.id + (design.match ? "?" + new URLSearchParams({ branch: design.match.branch_id, message: design.match.message_id }) : "");
    return <li key={design.id} className={"v2-list-item" + (selected ? " is-selected" : "")}>
      {selected ? <motion.span layoutId="v2-select-bar" className="v2-select-bar" aria-hidden="true"
        transition={reduced ? { duration: 0 } : { duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }} /> : null}
      <Link to={to} viewTransition className="v2-list-link" aria-current={selected ? "page" : undefined} onClick={props.onNavigate}>
        {design.thumbnail_url ? <img className="v2-list-thumbnail" src={design.thumbnail_url} alt="" loading="lazy" /> : null}
        <span className="v2-list-text">
          <span className="v2-list-title" title={design.title}>{design.pinned ? <IconPin size={16} stroke={1.75} aria-label={c("pinned")} /> : null}{design.title}</span>
          <span className="v2-list-meta">{design.last_activity_at ? <time dateTime={design.last_activity_at}>{time(design.last_activity_at)}</time> : null}
            {generating ? <span className="v2-state is-active" role="img" aria-label={c("generating")} title={c("generating")}><IconSparkles size={14} stroke={1.75} /></span>
              : needsAnswer ? <span className="v2-state is-attention" role="img" aria-label={c("needsAnswer")} title={c("needsAnswer")}><IconMessageCircleQuestion size={14} stroke={1.75} /></span>
              : design.has_cloud_draft ? <span className="v2-state" role="img" aria-label={c("hasDraft")} title={c("hasDraft")}><IconPencil size={14} stroke={1.75} /></span> : null}</span>
          {design.match ? <span className="v2-search-match">{design.match.text}</span> : null}
        </span>
      </Link>
      <DesignActions compact design={design} onUpdated={refresh} onRename={() => props.onRename(design)}
        deleteBlocked={Boolean(generating)} onRemoved={async () => { await props.onRemoved(design); await refresh(); }} />
    </li>;
  }

  return <aside className={"v2-sidebar" + (props.open ? " is-open" : "")} aria-label={c("designList")}>
    <div className="v2-brand"><img src={publicUrl("/brand/kigcraft-wordmark.png")} alt="KigCraft" width={1484} height={393} /><a className="v2-brand-github" href={GITHUB_URL} target="_blank" rel="noreferrer" aria-label={c("github")} title={c("github")}><IconBrandGithub size={20} stroke={1.75} /></a></div>
    <button type="button" className="v2-new" onClick={props.onNew}><IconPlus size={18} stroke={1.75} />{c("createDesign")}</button>
    <label className="v2-search"><IconSearch size={16} stroke={1.75} /><input aria-label={c("search")} placeholder={c("search")}
      value={search} maxLength={200} onChange={event => setSearch(event.target.value)} /></label>
    {view !== "active" ? <div className="v2-list-head">
      <button type="button" className="v2-quiet" onClick={() => setView("active")}><IconArrowLeft size={16} stroke={1.75} />{c("backToList")}</button>
      <strong>{c("archived")}</strong></div> : null}
    <nav aria-label={c("results")}>
      <LayoutGroup id="v2-list">
      {designs.isLoading ? <SidebarSkeleton /> : null}
      {groups.map(group => <section key={group.key} aria-label={c(group.key)}>
            <h2 className="v2-group-title">{c(group.key)}</h2><ul className="v2-list">{group.items.map(row)}</ul></section>)}
      <ErrorToast message={designs.error ? errorText(designs.error, language) : null}
        action={{ label: c("refreshList"), onClick: () => { void client.resetQueries({ queryKey: ["v2", userId, "designs", query, view], exact: true }); } }} />
      {!designs.isLoading && !designs.error && !items.length
        ? searching ? <div className="v2-list-empty" role="status"><p>{c("noResults")}</p><button type="button" onClick={() => setSearch("")}>{c("clearSearch")}</button></div>
          : <p className="v2-list-empty" role="status">{c("noDesigns")}</p> : null}
      {designs.hasNextPage && !designs.error ? <button type="button" disabled={designs.isFetching} onClick={() => void designs.fetchNextPage()}>
        {designs.isFetchingNextPage ? c("loading") : c("moreDesigns")}</button> : null}
      </LayoutGroup>
    </nav>
    {/* Archiving is hidden for now: conversations are kept up to the account limit and deleted when no longer needed. */}
    <AccountMenu {...props.account} />
  </aside>;
}
