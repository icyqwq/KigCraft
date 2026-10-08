import { useSyncExternalStore } from "react";
import { createRoot, type Root } from "react-dom/client";
import { dismissToast, registerHostMounter, snapshot, subscribe } from "./toastStore";
import "./toast.css";

export function ToastHost() {
  const current = useSyncExternalStore(subscribe, snapshot, snapshot);
  return <div className="v2-toast-host">
    {current.map(item => <div key={item.id} role={item.kind === "error" ? "alert" : "status"}
      className={"v2-toast" + (item.kind === "error" ? " is-error" : "") + (item.leaving ? " is-leaving" : "")}>
      <span>{item.message}</span>
      {item.action ? <button type="button" onClick={() => { const run = item.action!.onClick; dismissToast(item.id); run(); }}>{item.action.label}</button> : null}
    </div>)}
  </div>;
}

let root: Root | null = null, container: HTMLElement | null = null;
registerHostMounter(() => {
  if (typeof document === "undefined" || (container?.isConnected && root)) return;
  root?.unmount();
  container = document.createElement("div");
  container.dataset.toastRoot = "";
  // A modal dialog hides every other body child from assistive technology; a snackbar must stay announced above it.
  new MutationObserver(() => container?.removeAttribute("aria-hidden")).observe(container, { attributes: true, attributeFilter: ["aria-hidden"] });
  document.body.appendChild(container);
  root = createRoot(container);
  root.render(<ToastHost />);
});
