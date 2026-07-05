export const DEFAULT_LOCALE = "zh-CN" as const;
export const LOCALE_STORAGE_KEY = "kig-preview.locale.v1";

export const SUPPORTED_LOCALES = [
  { value: "zh-CN", label: "中文" },
  { value: "en", label: "English" },
  { value: "ja", label: "日本語" },
] as const;

export type AppLocale = (typeof SUPPORTED_LOCALES)[number]["value"];

const supportedLocaleValues = new Set<string>(SUPPORTED_LOCALES.map((locale) => locale.value));

export function normalizeLocale(value: unknown): AppLocale {
  return resolveSupportedLocale(value) ?? DEFAULT_LOCALE;
}

export function detectBrowserLocale(): AppLocale {
  if (typeof navigator === "undefined") return DEFAULT_LOCALE;

  const browserLanguages =
    Array.isArray(navigator.languages) && navigator.languages.length > 0
      ? navigator.languages
      : [navigator.language];
  for (const language of browserLanguages) {
    const locale = resolveSupportedLocale(language);
    if (locale) return locale;
  }

  return DEFAULT_LOCALE;
}

export function readStoredLocale(): AppLocale {
  if (typeof window === "undefined") return detectBrowserLocale();
  try {
    return resolveSupportedLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY)) ?? detectBrowserLocale();
  } catch {
    return detectBrowserLocale();
  }
}

export function writeStoredLocale(locale: AppLocale) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

function resolveSupportedLocale(value: unknown): AppLocale | null {
  if (typeof value !== "string") return null;
  if (supportedLocaleValues.has(value)) return value as AppLocale;

  const normalized = value.toLowerCase();
  if (normalized.startsWith("zh")) return "zh-CN";
  if (normalized.startsWith("en")) return "en";
  if (normalized.startsWith("ja")) return "ja";
  return null;
}
