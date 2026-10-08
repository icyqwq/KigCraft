import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

// Resolve from this module, not the shell's current directory; works on Node 22.
export const tokenSource = fileURLToPath(new URL("../src/ui/tokens.ts", import.meta.url));
const tokenOutput = new URL("../src/ui/tokens.css", import.meta.url);

export async function syncTokens({ check = false } = {}) {
  const source = await readFile(tokenSource, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  });
  const { renderTokenCss } = await import("data:text/javascript;base64," + Buffer.from(outputText).toString("base64"));
  const expected = renderTokenCss();
  const current = await readFile(tokenOutput, "utf8").catch(error => {
    if (error.code === "ENOENT") return "";
    throw error;
  });
  if (current.replace(/\r\n/g, "\n") === expected) return false;
  if (check) throw new Error("Design tokens CSS is stale. Run npm run tokens:generate and include src/ui/tokens.css.");
  await writeFile(tokenOutput, expected, "utf8");
  return true;
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  await syncTokens({ check: process.argv.includes("--check") });
}
