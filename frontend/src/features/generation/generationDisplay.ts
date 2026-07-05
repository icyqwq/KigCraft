const statusLabels: Record<string, string> = {
  accepted: "已选择",
  cancelled: "已取消",
  candidate_ready: "结果已完成",
  codex_generating: "生成中",
  failed: "生成失败",
  preparing_references: "准备素材",
  queued: "排队中",
  saving_outputs: "保存结果",
  succeeded: "生成完成",
};

const phraseLabels: Record<string, string> = {
  codex_generating: "生成中",
  generation_complete: "生成完成",
  generation_failed: "生成失败",
  preparing_references: "准备素材",
  saving_outputs: "保存结果",
};

const legacyMojibakeLabels: Record<string, string> = {
  "鐢熸垚涓?": "生成中",
  "鐢熸垚涓": "生成中",
  "鐢熸垚瀹屾垚": "生成完成",
  "鐢熸垚澶辫触": "生成失败",
  "鎺掗槦涓?": "排队中",
  "鎺掗槦涓": "排队中",
  "鍑嗗绱犳潗": "准备素材",
  "淇濆瓨缁撴灉": "保存结果",
  "閻㈢喐鍨氱€瑰本鍨?": "生成完成",
  "娣囨繂鐡ㄧ紒鎾寸亯": "保存结果",
  "宸查€夋嫨鍊欓€夛紝宸插仠姝㈠墿浣欑敓鎴?": "已选择候选，已停止剩余生成",
};

export function formatGenerationStatusLabel(status: string) {
  return statusLabels[normalizeGenerationKey(status)] ?? "处理中";
}

export function formatGenerationMessage(value: string | null | undefined) {
  const raw = value?.trim();
  if (!raw) return "";

  const legacyLabel = formatLegacyMojibake(raw);
  if (legacyLabel) return legacyLabel;

  const normalized = normalizeGenerationKey(raw);
  const mapped = phraseLabels[normalized] ?? statusLabels[normalized];
  if (mapped) return mapped;

  const candidateReadyMatch = raw.match(/(?:candidate|结果)\s*(\d+)(?:\s*\/\s*\d+)?\s*(?:ready|已完成)/i);
  if (candidateReadyMatch) {
    return `结果 ${candidateReadyMatch[1]} 已完成`;
  }

  const resultProgressMatch = raw.match(/结果\s*(\d+)(?:\/(\d+))?\s*已完成/);
  if (resultProgressMatch) {
    return resultProgressMatch[2]
      ? `结果 ${resultProgressMatch[1]}/${resultProgressMatch[2]} 已完成`
      : `结果 ${resultProgressMatch[1]} 已完成`;
  }

  return raw
    .replace(/\bcodex\s+generating\b/gi, "生成中")
    .replace(/\bcodex(?:\s+cli|\s+bridge)?\b/gi, "生成服务")
    .replace(/codex_/gi, "")
    .replace(/_/g, " ");
}

export function shouldShowGenerationEvent(type: string) {
  return type !== "token_usage";
}

function formatLegacyMojibake(raw: string) {
  const exact = legacyMojibakeLabels[raw];
  if (exact) return exact;

  if (/鐢熸垚.*瀹屾垚|閻㈢喐.*瑰本|瀹屾垚/.test(raw)) return "生成完成";
  if (/鐢熸垚.*澶辫触|澶辫触/.test(raw)) return "生成失败";
  if (/鎺掗槦|閹烘帡/.test(raw)) return "排队中";
  if (/鍑嗗|绱犳潗|閸戝棗/.test(raw)) return "准备素材";
  if (/淇濆瓨|娣囩繂|鎾寸亯/.test(raw)) return "保存结果";
  if (/鐢熸垚.*涓|閻㈢喐.*稉/.test(raw)) return "生成中";
  if (/宸查€夋嫨|鍊欓€/.test(raw)) return "已选择候选，已停止剩余生成";
  if (looksLikeLegacyMojibake(raw)) return "生成状态更新";

  return null;
}

function looksLikeLegacyMojibake(raw: string) {
  return /[鐢鎺閻闁閹鑽燧]/.test(raw) || (/[€?]/.test(raw) && /[濆瀹垚]/.test(raw));
}

function normalizeGenerationKey(value: string) {
  return value.trim().toLowerCase().replace(/[\s-]+/g, "_");
}
