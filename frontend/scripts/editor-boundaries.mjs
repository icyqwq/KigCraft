import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const slash = value => value.replaceAll("\\", "/");
const legacyAny = new Map([
  ["MuiBox as any", 1], ["MuiStack as any", 2], ["MuiButton as any", 1],
]);
const coreRoot = name => /^features\/editor\/(core|deformation|detection)\//.test(name) ||
  ["features/editor/EditorWorkspace.tsx", "features/editor/localGeneration.ts",
    "features/editor/imageRecipeMetadata.ts"].includes(name);
const businessModule = name => /^(api\/|features\/(auth|design|account|adminRecovery)\/)/.test(name) ||
  /^features\/editor\/(drafts\/(cloudApi|sync|PersistentEditor)|local\/adoption)/.test(name) ||
  name === "features/references/LegacyMaterialsPanel.tsx";

/** Includes type imports, re-exports and lazy imports: erased edges still couple source contracts. */
export function auditSources(sources, resolve) {
  const errors = [], graph = new Map(), exceptions = new Map();
  for (const [name, text] of sources) {
    const source = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true);
    const edges = [], requests = [];
    const at = node => source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
    const edge = (node, specifier, kind) => {
      if (!specifier || !ts.isStringLiteralLike(specifier)) {
        errors.push(name + ":" + at(node) + ": computed module import cannot be audited");
        return;
      }
      const target = resolve(specifier.text, name);
      if (target && sources.has(target)) edges.push({ target, kind, line: at(node) });
      else if (specifier.text.startsWith(".") && /\.(?:tsx?|jsx?)$/.test(specifier.text))
        errors.push(name + ":" + at(node) + ": unresolved source import " + specifier.text);
    };
    function visit(node) {
      if (node.kind === ts.SyntaxKind.AnyKeyword) {
        const expression = node.parent.getText(source);
        if (name === "ui/mui.tsx" && legacyAny.has(expression))
          exceptions.set(expression, (exceptions.get(expression) ?? 0) + 1);
        else errors.push(name + ":" + at(node) + ": explicit any");
      }
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier)
        edge(node, node.moduleSpecifier, node.isTypeOnly || node.importClause?.isTypeOnly ? "type" : "static");
      if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument))
        edge(node, node.argument.literal, "type");
      if (ts.isCallExpression(node)) {
        if (node.expression.kind === ts.SyntaxKind.ImportKeyword)
          edge(node, node.arguments[0], "dynamic");
        else if (ts.isIdentifier(node.expression) && node.expression.text === "require")
          edge(node, node.arguments[0], "require");
        const callee = node.expression.getText(source);
        if (/^(?:(?:window|globalThis)\.)?fetch$/.test(callee)) requests.push(at(node));
      }
      if (ts.isNewExpression(node) && /^(?:(?:window|globalThis)\.)?(XMLHttpRequest|WebSocket|EventSource)$/.test(node.expression.getText(source)))
        requests.push(at(node));
      ts.forEachChild(node, visit);
    }
    visit(source);
    // TS suppression comments are not a substitute for maintaining the contract.
    const scanner = ts.createScanner(ts.ScriptTarget.Latest, false, source.languageVariant, text);
    for (let token = scanner.scan(); token !== ts.SyntaxKind.EndOfFileToken; token = scanner.scan()) {
      if ((token === ts.SyntaxKind.SingleLineCommentTrivia || token === ts.SyntaxKind.MultiLineCommentTrivia) &&
          /@ts-(ignore|nocheck)\b/.test(scanner.getTokenText()))
        errors.push(name + ": forbidden TypeScript suppression");
    }
    graph.set(name, { edges, requests });
  }
  for (const [expression, count] of exceptions)
    if (count > legacyAny.get(expression)) errors.push("ui/mui.tsx: expanded legacy any: " + expression);

  const reachable = new Set(), queue = [...sources.keys()].filter(coreRoot).map(name => [name]);
  for (let index = 0; index < queue.length; index++) {
    const chain = queue[index], name = chain.at(-1);
    if (reachable.has(name)) continue;
    reachable.add(name);
    if (businessModule(name)) errors.push("editor business dependency: " + chain.join(" -> "));
    const module = graph.get(name);
    for (const line of module.requests)
      errors.push("editor network call: " + chain.join(" -> ") + ":" + line);
    for (const { target } of module.edges) queue.push([...chain, target]);
  }
  return {
    sourceFiles: sources.size, editorReachableFiles: reachable.size,
    legacyAny: Object.fromEntries(exceptions), errors,
    editorGraph: Object.fromEntries([...reachable].sort().map(name => [name, graph.get(name).edges])),
  };
}

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(item => {
    const full = path.join(directory, item.name);
    return item.isDirectory() ? sourceFiles(full) :
      /\.(ts|tsx)$/.test(item.name) && !/\.(test|spec)\.[^.]+$/.test(item.name) ? [full] : [];
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.join(frontend, "src");
  const sources = new Map(sourceFiles(root).map(file => [slash(path.relative(root, file)), readFileSync(file, "utf8")]));
  const config = ts.readConfigFile(path.join(frontend, "tsconfig.json"), ts.sys.readFile);
  const options = ts.parseJsonConfigFileContent(config.config, ts.sys, frontend).options;
  const report = auditSources(sources, (specifier, from) => {
    const resolved = ts.resolveModuleName(specifier, path.join(root, from), options, ts.sys).resolvedModule;
    return resolved ? slash(path.relative(root, resolved.resolvedFileName)) : undefined;
  });
  console.log(JSON.stringify(process.argv.includes("--json") ? report : {
    sourceFiles: report.sourceFiles, editorReachableFiles: report.editorReachableFiles,
    legacyAny: report.legacyAny, errors: report.errors,
  }, null, 2));
  if (report.errors.length) process.exitCode = 1;
}
