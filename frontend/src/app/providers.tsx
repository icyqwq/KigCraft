import { CssBaseline } from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import "../i18n";
import { normalizeLocale } from "../i18n/locales";
import { useTranslation } from "react-i18next";
import { kigTheme } from "./theme";

const queryClient = new QueryClient();

function LocaleDocument() {
  const { i18n } = useTranslation();

  useEffect(() => {
    const locale = normalizeLocale(i18n.language);
    document.documentElement.lang = locale;
    document.documentElement.dataset.locale = locale;
  }, [i18n.language]);

  return null;
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider theme={kigTheme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <LocaleDocument />
        {children}
      </QueryClientProvider>
    </ThemeProvider>
  );
}
