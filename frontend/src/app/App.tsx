import { useEffect } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { warmupAnimeLandmarkModels } from "../features/editor/deformation/animeLandmarkDetector";
import { AdminAuditPage } from "../pages/AdminAuditPage";
import { LandmarkQuantizationPage } from "../pages/LandmarkQuantizationPage";
import { WorkflowPage } from "../pages/WorkflowPage";

export function App() {
  useEffect(() => {
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let idleId: number | null = null;

    const warmup = () => {
      if (cancelled) return;
      void warmupAnimeLandmarkModels().catch((error: unknown) => {
        if (!cancelled) {
          console.warn("Anime landmark model warmup failed", error);
        }
      });
    };

    if ("requestIdleCallback" in window) {
      idleId = window.requestIdleCallback(warmup, { timeout: 2500 });
    } else {
      timeoutId = globalThis.setTimeout(warmup, 1200);
    }

    return () => {
      cancelled = true;
      if (idleId !== null && "cancelIdleCallback" in window) {
        window.cancelIdleCallback(idleId);
      }
      if (timeoutId !== null) {
        globalThis.clearTimeout(timeoutId);
      }
    };
  }, []);

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AdminAuditPage />} path="/admin/audit" />
        <Route element={<LandmarkQuantizationPage />} path="/landmark-quantization-test" />
        <Route element={<WorkflowPage />} path="*" />
      </Routes>
    </BrowserRouter>
  );
}
