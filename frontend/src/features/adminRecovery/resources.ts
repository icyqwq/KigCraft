import i18n from "../../i18n";
import en from "./copy-en";
import ja from "./copy-ja";
import zhCN from "./copy-zh-CN";

// This module is reached through the lazy admin page, never the public entry.
for (const [language, copy] of Object.entries({ en, ja, "zh-CN": zhCN })) {
  i18n.addResourceBundle(language, "v2Recovery", copy);
}
