import { useEffect } from "react";
import {ErrorToast} from "../../ui/ErrorToast";
import { useQuery } from "@tanstack/react-query";
import { Composer } from "./Composer";
import { designApi, DesignApiError, errorText, type Snapshot } from "./api";
import { useMessageCopy } from "./useMessageCopy";

/** Answer box directly under the assistant question. Only open questions are read from the server. */
export default function InlineQuestion({ ownerId, snapshot, operationId, maxInputImages, canGenerate, canLocalGenerate, busy, onSubmitted, onSignIn }: {
  ownerId: string; snapshot: Snapshot; operationId: string; maxInputImages: number; canGenerate: boolean; canLocalGenerate: boolean; busy: boolean;
  onSubmitted: (designId: string, branchId?: string) => Promise<void>; onSignIn: (action: () => void | Promise<void>) => void;
}) {
  const { c, language } = useMessageCopy();
  const preparation = useQuery({ queryKey: ["v2", ownerId, "question", snapshot.id, operationId],
    queryFn: ({ signal }) => designApi.prepareQuestion(operationId, signal), retry: false, gcTime: 0 });
  const value = preparation.data;
  const needsLogin = preparation.error instanceof DesignApiError && preparation.error.status === 401;
  useEffect(() => {
    if (!value || value.state !== "open") return;
    const timer = setTimeout(() => void preparation.refetch(), Math.min(Math.max(0, Date.parse(value.expires_at) - Date.now()) + 100, 2 ** 31 - 1));
    return () => clearTimeout(timer);
  }, [value?.expires_at, value?.state]);
  const localUnavailable = Boolean(value?.local_edit_mode && !canLocalGenerate);
  return <div className="v2-inline-question" role="group" aria-label={c("answer")}>
    {preparation.isPending ? <div className="v2-skeleton v2-skeleton-line" role="status" aria-label={c("loadingQuestion")} style={{ width: "60%", height: 44 }} /> : null}
    <ErrorToast message={preparation.isError ? errorText(preparation.error, language) : null}
      action={{ label: c(needsLogin ? "signInContinue" : "retry"), onClick: () => needsLogin ? onSignIn(async () => { await preparation.refetch(); }) : void preparation.refetch() }} />
    {value && value.branch_id === snapshot.branch.id ? <>
      {value.state !== "open" ? <p role="status">{c(value.state === "answered" ? "answered" : "expired")}</p> : null}
      {localUnavailable ? <p role="status">{c("answerLocalUnavailable")}</p> : null}
      {value.state === "open" ? <Composer snapshot={snapshot} userId={ownerId} editMessage={value}
        answerQuestion={{ operation_id: value.operation_id, version: value.version }} maxInputImages={maxInputImages}
        canGenerate={canGenerate && !localUnavailable} busy={busy}
        requireAuth={action => void action()} onSignIn={onSignIn} onSubmitted={onSubmitted} /> : null}
    </> : null}
  </div>;
}
