import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { DEFAULT_LOCALE, readStoredLocale } from "./locales";
import en from "./resources/en";
import ja from "./resources/ja";
import zhCN from "./resources/zh-CN";

void i18n.use(initReactI18next).init({
  fallbackLng: DEFAULT_LOCALE,
  interpolation: {
    escapeValue: false,
  },
  lng: readStoredLocale(),
  resources: {
    en: {
      translation: en,
    },
    ja: {
      translation: ja,
    },
    "zh-CN": {
      translation: zhCN,
    },
  },
});

export default i18n;
