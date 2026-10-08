import { LogoLoader } from "../../ui/LogoLoader";
import { lazy, Suspense, useRef } from "react";
import { IconPencil } from "@tabler/icons-react";
import { activeStates, type Operation, type Revision, type Snapshot } from "./api";
import { GenerationPlaceholder } from "./GenerationPlaceholder";
import { stageStep, useLingering } from "./placeholderState";
import { ImageCard } from "./ImageCard";
import { OperationFailureNotice } from "./OperationFailureNotice";
import { StreamingMessage } from "./StreamingMessage";
import type { historyTimeline } from "./historyTimeline";
import { useDesignCopy, type DesignCopyKey } from "./useDesignCopy";

const InlineMessageEditor = lazy(() => import("./InlineMessageEditor"));
const InlineQuestion = lazy(() => import("./InlineQuestion"));

const stateKeys: Record<string, DesignCopyKey> = {
  queued: "queued", reviewing: "reviewing", running: "running", publishing: "publishing",
  reconciling: "reconciling", cancelling: "cancelling", failed: "failed", cancelled: "cancelled",
  partial_success: "partial", awaiting_input: "awaiting",
};
export type TimelineCapabilities = { history_fork: boolean; new_operations: boolean; local_edits: boolean; max_input_images: number };
export type TimelineProps = {
  ownerId: string;
  data: Snapshot;
  /** Everything loaded so far, including older history pages. */
  snapshot: Snapshot;
  items: ReturnType<typeof historyTimeline>;
  searchTarget?: string;
  capabilities: TimelineCapabilities;
  canGenerate: boolean;
  active?: Operation;
  snapshotFailed: boolean;
  editingId: string | null;
  onStartEdit: (messageId: string) => void;
  onStopEdit: () => void;
  onStream: () => void;
  onOpenViewer: (revision: Revision) => void;
  onRegenerate: (revision: Revision) => void;
  onEditImage: (revision: Revision) => void;
  onCreateViewset: (revision: Revision) => void;
  onMakeHeadShell: (revision: Revision) => void;
  onSubmitted: (designId: string, branchId?: string, replace?: boolean) => Promise<void>;
  onSignIn: (action: () => void | Promise<void>) => void;
};

/** The conversation: bubbles, image cards, four-view cards, the pending picture and failure notice. */
export function Timeline(props: TimelineProps) {
  const { ownerId, data, snapshot, items, capabilities, canGenerate, active } = props;
  const { c } = useDesignCopy();
  const stateText = (state: string) => c(stateKeys[state] ?? "processing");
  // Images that arrive after the page was opened animate in; the ones loaded with it do not.
  const initial = useRef<Set<string> | null>(null);
  if (initial.current === null && items.length) initial.current = new Set(items.map(item => item.id));
  const pending = useLingering(active ? active : null, 220);
  const base = pending.value
    ? snapshot.revisions.find(revision => revision.id === pending.value!.base_revision_id) ?? snapshot.current_revision ?? undefined
    : undefined;
  const ratio = base?.width && base.height ? base.width / base.height : 1;
  const latestOperation = data.operations[0];
  return <>
    {items.map(item => item.type === "message" ? <article key={item.id} data-history-id={item.type + ":" + item.id}
        className={"v2-message is-" + item.message.role + (item.id === props.searchTarget ? " is-search-target" : "") + (item.id === props.editingId ? " is-editing" : "")}>
      <span className="v2-speaker v2-sr-only">{item.message.role === "user" ? c("you") : "KigCraft"}</span>
      {item.id === props.editingId ? <Suspense fallback={<div className="v2-skeleton v2-skeleton-line" style={{ width: "70%", height: 48 }} />}>
        <InlineMessageEditor key={ownerId + ":" + snapshot.branch.id + ":" + item.id} ownerId={ownerId} snapshot={snapshot} messageId={item.id}
          maxInputImages={capabilities.max_input_images} canGenerate={canGenerate} canLocalGenerate={capabilities.local_edits}
          busy={Boolean(active)} onCancel={props.onStopEdit} onSignIn={props.onSignIn}
          onSubmitted={async (id, branch) => { props.onStopEdit(); await props.onSubmitted(id, branch, true); }} />
      </Suspense> : <>
        <StreamingMessage ownerId={ownerId} designId={data.id} message={item.message} snapshotSequence={data.snapshot_sequence} onProgress={props.onStream} />
        {(data.questions ?? []).filter(q => q.messages.at(-1)?.message_id === item.message.id).map(q => q.state === "open"
          ? <Suspense key={q.operation_id} fallback={null}><InlineQuestion ownerId={ownerId} snapshot={snapshot} operationId={q.operation_id}
              maxInputImages={capabilities.max_input_images} canGenerate={canGenerate} canLocalGenerate={capabilities.local_edits}
              busy={Boolean(active)} onSignIn={props.onSignIn} onSubmitted={async (id, branch) => { await props.onSubmitted(id, branch); }} /></Suspense>
          : <p key={q.operation_id} className="v2-status" role="status">{c(q.state === "answered" ? "questionAnswered" : "questionExpired")}</p>)}
        {item.message.can_edit && capabilities.history_fork
          ? <button type="button" className="v2-message-edit-button is-hover-action" aria-label={c("editResend")} title={c("editResend")}
              disabled={Boolean(active) || Boolean(data.archived_at)} onClick={() => props.onStartEdit(item.message.id)}><IconPencil size={16} stroke={1.75} /></button>
          : null}
      </>}
    </article> : <ImageCard key={item.id} revision={item.revision} isNew={initial.current !== null && !initial.current.has(item.id)}
        canRegenerate={canGenerate && !active && capabilities.history_fork && Boolean(item.revision.can_regenerate)}
        canEdit={!data.archived_at && (item.revision.id === data.branch.current_revision_id || Boolean(item.revision.can_continue))}
        canViewsets={canGenerate && !active && capabilities.history_fork && item.revision.view === "front" && (item.revision.id === data.branch.current_revision_id || Boolean(item.revision.can_continue))}
        canMakeHeadShell={canGenerate && !active && capabilities.history_fork && Boolean(item.revision.can_continue)}
        viewsetsHint={item.revision.view !== "front" ? c("frontOnly") : undefined}
        onOpen={() => props.onOpenViewer(item.revision)} onRegenerate={() => props.onRegenerate(item.revision)}
        onEdit={() => props.onEditImage(item.revision)} onViewsets={() => props.onCreateViewset(item.revision)}
        onMakeHeadShell={() => props.onMakeHeadShell(item.revision)} />)}
    {pending.value ? <GenerationPlaceholder stage={stateText(pending.value.state)} step={stageStep(pending.value.state)} ratio={ratio} leaving={pending.leaving} /> : null}
    {!active && latestOperation && !["succeeded", "awaiting_input"].includes(latestOperation.state) && !activeStates.has(latestOperation.state)
      ? <OperationFailureNotice operation={latestOperation} messages={data.messages} stateText={stateText(latestOperation.state)}
          canRecover={Boolean(canGenerate && capabilities.new_operations && capabilities.history_fork && !props.snapshotFailed)} onRecover={props.onStartEdit} /> : null}
  </>;
}
