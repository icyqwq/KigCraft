import { useDesignCopy } from "../features/design/useDesignCopy";
import {ErrorToast} from "../ui/ErrorToast";
import { useComposerCopy } from "../features/design/useComposerCopy";
import { readViewer, viewerSearch, viewerReturn, revisionKey, type ViewerLocation } from "../features/design/viewerRoute";
import { readingKey } from "../features/design/conversationPosition";
import { useConversationViewport } from "../features/design/useConversationViewport";
import { NewContentButton } from "../features/design/NewContentButton";
import { DesignActions } from "../features/design/DesignActions";
import { historyTimeline } from "../features/design/historyTimeline";
import { Sidebar } from "../features/design/Sidebar";
import { Timeline } from "../features/design/Timeline";
import { DesignMissing, EmptyConversation } from "../features/design/EmptyStates";
import { EditorSkeleton, OverlaySkeleton, TimelineSkeleton } from "../features/design/Skeletons";
import { FirstStepChoice } from "../features/design/FirstStepChoice";
import { Lingering } from "../features/design/Lingering";
import { markTransition } from "../features/design/transitions";
import { loadEditorRoute, loadRevisionViewer, loadFourViewDialog, preloadOverlays } from "../features/design/overlays";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useLocation, useParams, useSearchParams } from "react-router-dom";
import { IconLayoutSidebar, IconPhotoPlus } from "@tabler/icons-react";
import { V2Dialog } from "../ui/V2Dialog";
import { toast } from "../ui/toastStore";
import "../ui/Toast";
import { useAuth } from "../features/auth/AuthProvider";
import type { useRequireAuth } from "../features/auth/useRequireAuth";
import { activeStates, designApi, DesignApiError, errorText, type Design, type Revision, type HistoryTarget } from "../features/design/api";
import type { ReferenceAttachment } from "../features/references/attachments";
import { Composer, type ComposerHandle, type FirstStep } from "../features/design/Composer";
import { useDesignEvents } from "../features/design/useDesignEvents";
import "../features/design/design.css";
const QuickLocalEditor = lazy(() => import("../features/editor/local/QuickLocalEditor").then(m => ({ default: m.QuickLocalEditor })));
const AccountDialog = lazy(() => import("../features/account/AccountDialog"));
const FourViewDialog = lazy(loadFourViewDialog);
const RevisionViewer = lazy(loadRevisionViewer);
const EditorRoute = lazy(loadEditorRoute);
const LegacySources = lazy(() => import("../features/design/LegacySources"));

export function DesignPage({ auth }: { auth: ReturnType<typeof useRequireAuth> }) {
  const { currentUser, authSessionEpoch, logout } = useAuth();
  const { c, language } = useDesignCopy();
  const composerCopy = useComposerCopy().c;
  const { designId } = useParams();
  const [searchParams] = useSearchParams();
  const branchId = searchParams.get("branch") ?? undefined;
  const messageId = searchParams.get("message") ?? undefined;
  const navigate = useNavigate();
  const location = useLocation();
  const client = useQueryClient();
  const [sidebar, setSidebar] = useState(false);
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem("v2-sidebar-collapsed") === "1"; } catch { return false; } });
  function toggleSidebar() {
    if (window.matchMedia?.("(max-width:767px)").matches) { setSidebar(open => !open); return; }
    setCollapsed(value => { const next = !value; try { localStorage.setItem("v2-sidebar-collapsed", next ? "1" : "0"); } catch { /* preference only */ } return next; });
  }
  const [editingId, setEditingId] = useState<string | null>(null);
  const viewsetSource = searchParams.get("create-viewset");
  const viewerOpen = searchParams.has("viewer");
  const viewerLocation = readViewer(searchParams);
  const [localFile, setLocalFile] = useState<File | null>(null);
  const editorId = searchParams.get("edit");
  const editorDraftId = searchParams.get("draft") ?? undefined;
  const editorOpen = editorId !== null;
  const [historyAction, setHistoryAction] = useState<(HistoryTarget & { designId: string; branchId: string }) | null>(null);
  const [autoSend, setAutoSend] = useState<{ id: string; text: string; target: HistoryTarget; designId: string; branchId: string; kind?: "head_shell" | "four_view"; attachments?: ReferenceAttachment[] } | null>(null);
  const [firstStep, setFirstStep] = useState<FirstStep>("reference");
  const [accountView, setAccountView] = useState<"account" | "about" | null>(null);
  const [rename, setRename] = useState<{ design: Design; title: string } | null>(null);
  const [error, setError] = useState<unknown>("");
  const composer = useRef<ComposerHandle>(null), dragDepth = useRef(0), [dragging, setDragging] = useState(false);
  const hasFiles = (event: React.DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes("Files");
  const userId = currentUser?.id;
  const capability = useQuery({ queryKey: ["v2-capabilities"], queryFn: designApi.capabilities, retry: false });
  const enabled = capability.data?.enabled === true;
  const snapshot = useQuery({ queryKey: ["v2", userId, "snapshot", designId, branchId],
    queryFn: () => designApi.snapshot(designId!, branchId), enabled: enabled && Boolean(userId && designId), retry: false,
    refetchInterval: entry => entry.state.data && (activeStates.has(entry.state.data.design_active_operation?.state ?? "") || entry.state.data.operations.some(o => activeStates.has(o.state))) ? 10000 : false });
  const data = snapshot.data;
  const missing = snapshot.error instanceof DesignApiError && snapshot.error.status === 404;
  const history = useInfiniteQuery({
    queryKey: ["v2", userId, "history", designId, data?.branch.id, messageId ?? null],
    queryFn: ({ pageParam, signal }) => designApi.history(data!.branch.id, pageParam, signal, messageId),
    initialPageParam: null as string | null,
    getNextPageParam: page => page.next_cursor ?? undefined,
    enabled: Boolean(userId && data?.history), retry: false, staleTime: 10000,
    initialData: data?.history && !messageId ? { pages: [data.history], pageParams: [null] } : undefined,
    initialDataUpdatedAt: snapshot.dataUpdatedAt,
  });
  const timeline = useMemo(() => historyTimeline(history.data?.pages, messageId ? undefined : data), [history.data, data, messageId]);
  const availableSnapshot = useMemo(() => {
    if (!data) return undefined;
    const revisions = timeline.flatMap(item => item.type === "image" ? [item.revision] : []);
    if (data.current_revision && !revisions.some(r => r.id === data.current_revision!.id)) revisions.push(data.current_revision);
    return { ...data, revisions, messages: timeline.flatMap(item => item.type === "message" ? [item.message] : []) };
  }, [data, timeline]);
  const conversation = useRef<HTMLElement>(null);
  const latestTimeline = useMemo(() => historyTimeline(undefined, data), [data]);
  const viewport = useConversationViewport({
    container: conversation, scope: userId && data ? readingKey(userId, data.id, data.branch.id, messageId ?? null) : null,
    entry: viewerReturn(location.state?.editorReturn ?? location.state?.viewerReturn ?? location.state?.viewsetCreateReturn, userId, data?.id)?.key ?? location.key,
    ready: Boolean(data && (!messageId || history.data)), focus: messageId,
    forceBottom: location.state?.conversationBottom === true, items: timeline, latest: latestTimeline,
    fetching: history.isFetching, fetchingMore: history.isFetchingNextPage, hasMore: Boolean(history.hasNextPage),
    failed: history.isError, fetchMore: history.fetchNextPage,
  });
  function goLatest() {
    if (messageId && data) navigate("/v2/" + data.id + "?branch=" + data.branch.id, { state: { conversationBottom: true } });
    else viewport.toBottom();
  }
  const connected = useDesignEvents(userId, designId, data?.snapshot_sequence);
  useEffect(() => { preloadOverlays(); }, []);
  useEffect(() => { setEditingId(null); setHistoryAction(null); setAutoSend(null); setError(""); }, [designId, branchId, userId]);
  const active = data?.design_active_operation ?? data?.operations.find(o => activeStates.has(o.state));
  // The editor's local generation runs beside the conversation: it shows no placeholder there and only pauses the input.
  const localEditing = active?.kind === "local_edit";
  // Until the first step has produced something, the message box is not offered; only stopping is.
  const firstStepRunning = Boolean(data && availableSnapshot && availableSnapshot.revisions.length === 0 && active);
  const canGenerate = enabled && capability.data?.generation === true && !data?.archived_at;
  function leaveDesign() { markTransition("design"); navigate("/v2", { viewTransition: true }); setSidebar(false); setError(""); }
  async function refresh() { await client.invalidateQueries({ queryKey: ["v2"] }); }
  async function submitted(id: string, branch?: string, replace = false) {
    setHistoryAction(null); setAutoSend(null); await refresh();
    navigate("/v2/" + id + (branch ? "?branch=" + encodeURIComponent(branch) : ""), { replace, state: { conversationBottom: true } });
  }
  function chooseHistory(revision: Revision, kind: "continue" | "regenerate") {
    if (!data || active) return;
    setHistoryAction({ id: crypto.randomUUID(), kind, revisionId: revision.id, imageUrl: revision.image_url,
      branchVersion: data.branch.state_version, designId: data.id, branchId: data.branch.id });
    if (viewerOpen) closeViewer();
  }
  /** From a reference image: continue from it and send the head-shell request through the normal submit path. */
  function makeHeadShell(revision: Revision) {
    if (!data || active) return;
    setAutoSend({ id: crypto.randomUUID(), text: composerCopy("headShellPrompt"), designId: data.id, branchId: data.branch.id,
      target: { id: crypto.randomUUID(), kind: "continue", revisionId: revision.id, imageUrl: revision.image_url, branchVersion: data.branch.state_version } });
    if (viewerOpen) closeViewer();
  }
  function openEditor(revision: Revision) {
    if (!data || !userId) return;
    client.setQueryData(revisionKey(userId, data.id, revision.id), revision);
    const params = viewerSearch(searchParams, null); params.delete("create-viewset"); params.delete("draft"); params.set("edit", revision.id); params.set("branch", revision.view === "front" ? data.branch.id : revision.branch_id);
    navigate({ pathname: location.pathname, search: "?" + params.toString() },
      { state: { ...location.state, editorReturn: { owner: userId, design: data.id, key: location.key } } });
  }
  function closeEditor() {
    if (viewerReturn(location.state?.editorReturn, userId, data?.id)) { navigate(-1); return; }
    const params = new URLSearchParams(searchParams); params.delete("edit"); params.delete("draft");
    navigate({ pathname: location.pathname, search: params.toString() ? "?" + params.toString() : "" }, { replace: true });
  }
  function openViewer(revision: Revision, allVersions = false) {
    if (!data || !userId) return;
    client.setQueryData(revisionKey(userId, data.id, revision.id), revision);
    const params = viewerSearch(searchParams, { origin: revision.id, selected: revision.id, comparison: null, allVersions });
    params.set("branch", data.branch.id);
    navigate({ pathname: location.pathname, search: "?" + params.toString() },
      { viewTransition: true, state: { ...location.state, viewerReturn: { owner: userId, design: data.id, key: location.key } } });
  }
  function openAllVersions() {
    const latest = data?.current_revision ?? availableSnapshot?.revisions.at(-1);
    if (latest) openViewer(latest, true);
  }
  function changeViewer(value: ViewerLocation, revisions: Revision[]) {
    if (!data || !userId) return;
    for (const revision of revisions) client.setQueryData(revisionKey(userId, data.id, revision.id), revision);
    navigate({ pathname: location.pathname, search: "?" + viewerSearch(searchParams, value).toString() },
      { replace: true, state: location.state });
  }
  function closeViewer() {
    if (viewerReturn(location.state?.viewerReturn, userId, data?.id)) { navigate(-1); return; }
    const params = viewerSearch(searchParams, null);
    navigate({ pathname: location.pathname, search: params.toString() ? "?" + params.toString() : "" }, { replace: true, state: location.state });
  }
  function openViewsetCreate(revision: Revision) {
    if (!data || !userId || revision.view !== "front") return;
    client.setQueryData(revisionKey(userId, data.id, revision.id), revision);
    const params = viewerSearch(searchParams, null); params.delete("edit"); params.delete("viewset"); params.delete("views");
    params.set("create-viewset", revision.id); params.set("branch", data.branch.id);
    markTransition("workspace");
    navigate({ pathname: location.pathname, search: "?" + params.toString() },
      { viewTransition: true, state: { viewsetCreateReturn: { owner: userId, design: data.id, key: location.key } } });
  }
  function closeViewsetCreate() {
    if (viewerReturn(location.state?.viewsetCreateReturn, userId, data?.id)) { navigate(-1); return; }
    const params = new URLSearchParams(searchParams); params.delete("create-viewset");
    navigate({ pathname: location.pathname, search: "?" + params.toString() }, { replace: true });
  }
  // A four-view sheet is an ordinary generation turn: hand it to the composer, which sends it from the chosen front version.
  function makeFourView({ revision, text, attachments }: { revision: Revision; text: string; attachments: ReferenceAttachment[] }) {
    if (!data || !userId || active) return;
    setAutoSend({ id: crypto.randomUUID(), kind: "four_view", text: text || composerCopy("fourViewPrompt"), attachments, designId: data.id, branchId: data.branch.id,
      target: { id: crypto.randomUUID(), kind: "continue", revisionId: revision.id, imageUrl: revision.image_url, branchVersion: data.branch.state_version } });
    const params = viewerSearch(searchParams, null); params.delete("create-viewset");
    navigate({ pathname: location.pathname, search: params.toString() ? "?" + params.toString() : "" }, { replace: true, state: { conversationBottom: true } });
  }
  function stopActive() {
    if (!active || active.state === "cancelling") return;
    void designApi.cancel(active.id).then(refresh).catch(failure => setError(failure || "request_failed"));
  }
  async function saveName() {
    if (!rename?.title.trim()) return;
    try { await designApi.rename(rename.design, rename.title.trim()); setRename(null); toast(c("saved")); await refresh(); }
    catch (failure) { setError(failure || "request_failed"); }
  }
  return <div className={"v2-shell" + (collapsed ? " is-collapsed" : "")}>
    <a className="v2-skip" href="#v2-composer" onClick={event => { event.preventDefault(); document.querySelector<HTMLTextAreaElement>(".v2-compose-dock textarea")?.focus(); }}>{c("skipToComposer")}</a>
    {sidebar ? <button className="v2-scrim" aria-label={c("closeList")} onClick={() => setSidebar(false)} /> : null}
    <Sidebar userId={userId} enabled={enabled} selectedId={designId} open={sidebar}
      account={{ user: currentUser ? { id: currentUser.id, email: currentUser.email } : null, epoch: authSessionEpoch,
        onLogin: () => { setSidebar(false); auth.openAuthDialog(); },
        onOpenAccount: view => { setSidebar(false); setAccountView(view); },
        onLogout: () => { logout(); leaveDesign(); } }}
      onNavigate={() => { markTransition("design"); setSidebar(false); }} onNew={leaveDesign} onClose={() => setSidebar(false)}
      onRename={design => setRename({ design, title: design.title })}
      onRemoved={async design => { if (design.id === designId) leaveDesign(); }} />
    <main className="v2-main"
      onDragEnter={event => { if (hasFiles(event)) { dragDepth.current += 1; setDragging(true); } }}
      onDragOver={event => { if (hasFiles(event)) event.preventDefault(); }}
      onDragLeave={event => { if (hasFiles(event)) { dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false); } }}
      onDrop={event => { if (!hasFiles(event)) return; event.preventDefault(); dragDepth.current = 0; setDragging(false); composer.current?.addFiles(event.dataTransfer.files); }}>
      {dragging ? <div className="v2-drop-overlay" aria-hidden="true"><span>{c("dropHere")}</span></div> : null}
      <header className="v2-topbar">
        <button className="v2-quiet v2-icon v2-menu-toggle" aria-label={c(sidebar ? "closeList" : "openList")} aria-expanded={sidebar || !collapsed} onClick={toggleSidebar}><IconLayoutSidebar size={18} stroke={1.75} /></button>
        <h1>{data?.title ?? c("untitled")}</h1>
        <div className="v2-topbar-actions">
          {!designId ? <label className="v2-quiet v2-local-entry-top"><IconPhotoPlus size={16} stroke={1.75} />{c("editLocal")}
            <input type="file" hidden accept="image/png,image/jpeg,image/webp" data-testid="local-image-input"
              onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) setLocalFile(file); }} /></label> : null}
          {data ? <DesignActions key={userId + ":" + data.id} design={data} operation={active ?? undefined} onUpdated={refresh}
            onRename={() => setRename({ design: data, title: data.title })} onHistory={openAllVersions}
            onRemoved={async () => { leaveDesign(); await refresh(); }} /> : null}
        </div>
      </header>
      {data?.legacy_imported ? <Suspense fallback={null}><div className="v2-archive-status"><LegacySources designId={data.id} /></div></Suspense> : null}
      {data?.archived_at ? <p className="v2-archive-status" role="status"><span>{c(active ? "archivedActive" : "archivedNote")}</span>
        <button className="v2-quiet" onClick={() => { void designApi.update(data, { archived: false }).then(() => { toast(c("saved")); return refresh(); }).catch(failure => setError(failure || "request_failed")); }}>{c("unarchive")}</button></p> : null}
      {messageId && data ? <div className="v2-search-location"><span>{c("searchLocation")}</span><button onClick={goLatest}>{c("latest")}</button></div> : null}
      <section ref={conversation} onScroll={viewport.onScroll} onWheel={viewport.onUserScroll} onTouchMove={viewport.onUserScroll} onKeyDown={event => { if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key) && !(event.target as HTMLElement).closest("button,a,input,textarea,select")) viewport.onUserScroll(); }} className="v2-conversation" aria-label={c("conversation")}>
        {!enabled && !capability.isLoading ? <p role="status">{c("unavailable")}</p> : null}
        {missing ? <DesignMissing /> : capability.error || snapshot.error ? <ErrorToast message={errorText(capability.error ?? snapshot.error, language)} /> : null}
        {snapshot.isLoading ? <TimelineSkeleton /> : null}
        {history.hasNextPage ? <div className="v2-history-more"><button disabled={history.isFetching} onClick={viewport.loadOlder}>
          {history.isFetchingNextPage ? c("loading") : history.isFetchNextPageError ? c("retryLoad") : c("older")}</button></div> : null}
        <ErrorToast message={history.isError ? c("historyFailed") : null} action={!history.isFetchNextPageError ? { label: c("retry"), onClick: () => void history.refetch() } : undefined} />
        {history.data?.pages.some(page => page.ancestry_incomplete) ? <p role="status" className="v2-status">{c("ancestryUnavailable")}</p> : null}
        {!designId ? <EmptyConversation /> : null}
        {data && userId && availableSnapshot ? <Timeline key={data.id + ":" + data.branch.id} ownerId={userId} data={data} snapshot={availableSnapshot} items={timeline} searchTarget={messageId}
          capabilities={{ history_fork: capability.data?.history_fork === true, new_operations: capability.data?.new_operations === true,
            local_edits: capability.data?.local_edits === true, max_input_images: capability.data?.max_input_images ?? 8 }}
          canGenerate={canGenerate} active={localEditing ? undefined : active ?? undefined} snapshotFailed={snapshot.isError} editingId={editingId} onStartEdit={setEditingId} onStopEdit={() => setEditingId(null)}
          onStream={viewport.onStream} onOpenViewer={revision => openViewer(revision)} onRegenerate={revision => chooseHistory(revision, "regenerate")} onEditImage={openEditor}
          onCreateViewset={openViewsetCreate} onMakeHeadShell={makeHeadShell}
          onSubmitted={submitted} onSignIn={auth.openAuthDialog} /> : null}
        {!connected ? <p role="status" className="v2-status">{c("reconnecting")}</p> : null}
        <ErrorToast message={error ? errorText(error, language) : null} />
      </section>
      <NewContentButton signal={viewport.signal} onClick={goLatest} />
      <footer id="v2-composer" className="v2-compose-dock">{localEditing ? <p className="v2-local-lock" role="status"><span className="kc-spinner" aria-hidden="true" />{composerCopy("localEditing")}</p> : null}{!designId ? <FirstStepChoice value={firstStep} onChange={setFirstStep} /> : null}{firstStepRunning ? <div className="v2-first-step"><button type="button" disabled={active?.state === "cancelling"} onClick={stopActive}>{composerCopy("stop")}</button></div>
        : !designId || data ? <div style={{ display: "contents" }} inert={localEditing ? true : undefined}><Composer key={(designId ?? "new") + ":" + (data?.branch.id ?? "new")} snapshot={availableSnapshot} userId={userId} canGenerate={canGenerate}
          maxInputImages={capability.data?.max_input_images} busy={Boolean(active) || Boolean(designId && !data)} handle={composer} targetKind={!designId ? firstStep : undefined}
          onStop={active && !localEditing ? stopActive : undefined} stopping={active?.state === "cancelling"} requireAuth={auth.requireAuth} onSignIn={auth.openAuthDialog} onSubmitted={submitted}
          historyAction={historyAction?.designId === data?.id && historyAction?.branchId === data?.branch.id ? historyAction : null}
          autoSend={autoSend && autoSend.designId === data?.id && autoSend.branchId === data?.branch.id ? autoSend : undefined} /></div> : null}</footer>
    </main>
    <Lingering when={viewsetSource !== null && Boolean(userId && data)} render={closing => <Suspense fallback={<OverlaySkeleton />}><FourViewDialog closing={closing}
      key={userId + ":" + data?.id + ":" + viewsetSource} owner={userId!} design={data!.id} source={viewsetSource ?? ""}
      canGenerate={canGenerate && !snapshot.isError} busy={Boolean(active)} onClose={closeViewsetCreate} onSubmit={makeFourView} /></Suspense>} />
    <Lingering when={viewerOpen && !editorOpen && viewsetSource === null && Boolean(userId && data) && !snapshot.isError} render={closing => <Suspense fallback={<OverlaySkeleton />}><RevisionViewer closing={closing} key={userId + ":" + data?.id + ":" + data?.branch.id + ":" + viewerLocation?.origin}
      ownerId={userId!} designId={data!.id} location={viewerLocation} known={availableSnapshot?.revisions ?? []}
      canContinue={Boolean(capability.data?.history_fork) && !active && !data?.archived_at}
      onChange={changeViewer} onContinue={revision => chooseHistory(revision, "continue")} onClose={closeViewer} /></Suspense>} />
    <V2Dialog open={rename !== null} title={c("rename")} onClose={() => setRename(null)}
      actions={<><button type="button" onClick={() => setRename(null)}>{c("cancel")}</button>
        <button className="v2-primary" type="submit" form="v2-rename-form" disabled={!rename?.title.trim()}>{c("save")}</button></>}>
      <form id="v2-rename-form" onSubmit={event => { event.preventDefault(); void saveName(); }}>
        <input aria-label={c("designName")} value={rename?.title ?? ""} maxLength={200} onChange={event => setRename(current => current && { ...current, title: event.target.value })} />
      </form>
    </V2Dialog>
    {accountView && currentUser ? <Suspense fallback={null}><AccountDialog key={currentUser.id + ":" + authSessionEpoch + ":" + accountView} initialView={accountView}
      onClose={() => setAccountView(null)} onLogout={() => { setAccountView(null); leaveDesign(); }} /></Suspense> : null}
    <Lingering when={editorOpen && viewsetSource === null && Boolean(data && userId && availableSnapshot)} render={closing => <Suspense fallback={<EditorSkeleton />}>
      <EditorRoute closing={closing} key={userId + ":" + data?.id + ":" + data?.branch.id + ":" + editorId + ":" + editorDraftId} cloudDraftId={editorDraftId} ownerId={userId!} snapshot={availableSnapshot!} revisionId={editorId ?? ""}
        invalid={Boolean(editorDraftId && !/^[a-zA-Z0-9_-]{1,100}$/.test(editorDraftId)) || viewerOpen || !branchId || !/^[a-zA-Z0-9_-]{1,100}$/.test(editorId ?? "")} snapshotUnavailable={snapshot.isError}
        maxInputImages={capability.data?.max_input_images} canLocalGenerate={canGenerate && capability.data?.local_edits === true} busy={Boolean(active)}
        onSignIn={auth.openAuthDialog} onSubmitted={refresh} onClose={closeEditor} onSaved={branch => submitted(data!.id, branch, true)} />
    </Suspense>} />
    {localFile ? <Suspense fallback={<EditorSkeleton />}><QuickLocalEditor key={localFile.name + localFile.lastModified} file={localFile} onClose={() => setLocalFile(null)} /></Suspense> : null}
  </div>;
}
