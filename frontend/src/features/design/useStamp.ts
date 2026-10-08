import { imageKindOf, type Revision } from "./api";
import { useDesignCopy } from "./useDesignCopy";

/** Stamp text such as "Head shell v3" / "Reference v2"; plain "v3" never appears without its kind. */
export function useStamp() {
  const { c } = useDesignCopy();
  return (revision: Pick<Revision, "number" | "image_kind">) => {
    if (revision.number === undefined) return c("maskDesign");
    return c(imageKindOf(revision) === "reference" ? "stampReference" : "stampHeadShell", { number: revision.number });
  };
}
