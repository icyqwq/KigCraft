import { publicUrl } from "../../ui/publicUrl";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useDesignCopy, type DesignCopyKey } from "./useDesignCopy";

/** The wordmark draws itself: outlines first, then the fill. The animation and its reduced-motion still live in the SVG. */
function Wordmark() {
  return <img className="v2-wordmark-draw" src={publicUrl("/brand/kigcraft-wordmark-draw.svg")} alt="KigCraft" width="1484" height="393" draggable={false} />;
}

export const STARTER_LINES = ["startIdea1", "startIdea2", "startIdea3", "startIdea4", "startIdea5", "startIdea6",
  "startIdea7", "startIdea8", "startIdea9"] as const satisfies readonly DesignCopyKey[];

/** New conversation: the mark and one kigurumi line, picked at random each time the page opens. */
export function EmptyConversation() {
  const { c } = useDesignCopy();
  const [line] = useState(() => STARTER_LINES[Math.floor(Math.random() * STARTER_LINES.length)]);
  return <div className="v2-empty">
    <Wordmark />
    <h2>{c(line)}</h2>
  </div>;
}

/** The address points at a design that is missing or removed. */
export function DesignMissing() {
  const { c } = useDesignCopy();
  return <div className="v2-empty" role="alert">
    <Wordmark />
    <h2>{c("designMissing")}</h2>
    <Link className="v2-local-entry" to="/v2">{c("backToDesigns")}</Link>
  </div>;
}
