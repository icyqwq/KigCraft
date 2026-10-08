import { useComposerCopy } from "./useComposerCopy";
import type { FirstStep } from "./Composer";

/** What to make first, for the first message of a design. Sits above the message box, at its top left. */
export function FirstStepChoice({ value, onChange }: { value: FirstStep; onChange: (value: FirstStep) => void }) {
  const { c } = useComposerCopy();
  return <div role="radiogroup" aria-label={c("stepLabel")} className="v2-first-step-choice">
    {(["reference", "head_shell"] as const).map(kind => <button key={kind} type="button" role="radio" aria-checked={value === kind}
      className={value === kind ? "is-selected" : ""} onClick={() => onChange(kind)}>{c(kind === "reference" ? "stepReference" : "stepHeadShell")}</button>)}
  </div>;
}
