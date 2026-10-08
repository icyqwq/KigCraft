import {useEditorCopy} from "./useEditorCopy";
import type { EditRecipe } from "./deformation/recipe";

export function ImportedImageRecord({ recipe, fileName }: { recipe?: EditRecipe | null; fileName: string }) {
  const {c}=useEditorCopy();
  if (!recipe) return null;
  function downloadRecord() {
    const blob = new Blob([JSON.stringify({ schema: "kigcraft.import-record", version: 1,
      sourceFileName: fileName, importMode: "flattened", originalRootAvailable: false, sourceRecipe: recipe }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url;
    link.download = fileName.replace(/[\\/]/g, "_") + ".edit-record.json";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  return <details data-testid="imported-image-record">
    <summary>{c("importRecord")}</summary>
    <p>{c("flatNote")}</p>
    <button type="button" onClick={downloadRecord}>{c("downloadRecord")}</button>
  </details>;
}
