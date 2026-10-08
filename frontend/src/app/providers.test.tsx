import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import i18n from "../i18n";
import { Providers } from "./providers";

describe("Providers", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh-CN");
    delete document.documentElement.dataset.locale;
  });

  afterEach(async () => {
    cleanup();
    await i18n.changeLanguage("zh-CN");
    delete document.documentElement.dataset.locale;
  });

  it("synchronizes the document locale when the app language changes", async () => {
    render(
      <Providers>
        <div>content</div>
      </Providers>,
    );

    await waitFor(() => expect(document.documentElement.lang).toBe("zh-CN"));
    expect(document.documentElement.dataset.locale).toBe("zh-CN");

    await i18n.changeLanguage("ja");

    await waitFor(() => expect(document.documentElement.lang).toBe("ja"));
    expect(document.documentElement.dataset.locale).toBe("ja");
  });
});
