import "@testing-library/jest-dom/vitest";
import {afterEach,beforeEach} from "vitest";
import {clearToasts} from "../ui/toastStore";
import i18n from "../i18n";

beforeEach(async()=>{await i18n.changeLanguage("zh-CN");});
// Snackbars live outside any rendered tree; never let one leak into the next test.
afterEach(()=>clearToasts());
