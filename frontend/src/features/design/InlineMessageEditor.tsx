import { useQuery } from "@tanstack/react-query";
import {ErrorToast} from "../../ui/ErrorToast";
import { Composer } from "./Composer";
import { MessageContent } from "./MessageContent";
import { designApi, DesignApiError, errorText, type Snapshot } from "./api";
import { useMessageCopy } from "./useMessageCopy";
import { useDesignCopy } from "./useDesignCopy";

/** The own bubble turns into an editor in place; sending follows the new branch and no plan is shown. */
export default function InlineMessageEditor({ ownerId, snapshot, messageId, maxInputImages, canGenerate, canLocalGenerate, busy, onCancel, onSubmitted, onSignIn }: {
  ownerId: string; snapshot: Snapshot; messageId: string; maxInputImages: number; canGenerate: boolean; canLocalGenerate: boolean; busy: boolean;
  onCancel: () => void; onSubmitted: (designId: string, branchId?: string) => Promise<void>; onSignIn: (action: () => void | Promise<void>) => void;
}) {
  const { c, language } = useMessageCopy();
  const design = useDesignCopy().c;
  const preparation = useQuery({ queryKey: ["v2-message-edit", ownerId, snapshot.id, snapshot.branch.id, messageId],
    queryFn: ({ signal }) => designApi.prepareMessageEdit(snapshot.branch.id, messageId, signal), retry: false, gcTime: 0, staleTime: Infinity });
  const value = preparation.data;
  const needsLogin = preparation.error instanceof DesignApiError && preparation.error.status === 401;
  const localUnavailable = Boolean(value?.local_edit_mode && !canLocalGenerate);
  return <div className="v2-inline-editor" role="group" aria-label={c("edit")}>
    {preparation.isPending ? <div className="v2-skeleton v2-skeleton-line" role="status" aria-label={c("loadingMessage")} style={{ width: "70%", height: 48 }} /> : null}
    <ErrorToast message={preparation.isError ? errorText(preparation.error, language) : null}
      action={{ label: c(needsLogin ? "signInContinue" : "retry"), onClick: () => needsLogin ? onSignIn(async () => { await preparation.refetch(); }) : void preparation.refetch() }} />
    {preparation.isError ? <button type="button" className="v2-quiet" onClick={onCancel}>{design("cancel")}</button> : null}
    {value ? <>
      {value.local_edit_mode ? <p className="v2-status">{c(value.local_edit_mode === "lock_outside" ? "lockMode" : "preserveMode")}</p> : null}
      {value.annotations.length ? <MessageContent parts={[{ type: "annotations", annotations: value.annotations }]} /> : null}
      {localUnavailable ? <p role="status">{c("editLocalUnavailable")}</p> : null}
      <Composer snapshot={snapshot} userId={ownerId} editMessage={value} maxInputImages={maxInputImages} onCancel={onCancel}
        canGenerate={canGenerate && !localUnavailable} busy={busy} requireAuth={action => void action()} onSignIn={onSignIn} onSubmitted={onSubmitted} />
    </> : null}
  </div>;
}
