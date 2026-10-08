import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import { IconCheck, IconChevronRight } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import { designApi } from "./api";
import { getLocaleLabelKey, normalizeLocale, SUPPORTED_LOCALES, writeStoredLocale, type AppLocale } from "../../i18n/locales";
import { useDesignCopy } from "./useDesignCopy";

export type AccountMenuProps = {
  user: { id: string; email: string } | null;
  epoch: number;
  onLogin: () => void;
  onOpenAccount: (view: "account" | "about") => void;
  onLogout: () => void;
};

/** Square initial, email and quota number; opens the account menu with language as a submenu. */
export function AccountMenu({ user, epoch, onLogin, onOpenAccount, onLogout }: AccountMenuProps) {
  const { t, i18n } = useTranslation();
  const { c } = useDesignCopy();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [language, setLanguage] = useState<HTMLElement | null>(null);
  const quota = useQuery({ queryKey: ["v2", user?.id, "quota", epoch], queryFn: designApi.quota, retry: false, enabled: Boolean(user) });
  const current = normalizeLocale(i18n.language);
  if (!user) return <div className="v2-sidebar-foot"><button type="button" className="v2-account" onClick={onLogin}>{t("auth.login")}</button></div>;
  const close = () => { setAnchor(null); setLanguage(null); };
  async function choose(locale: AppLocale) {
    writeStoredLocale(locale);
    await i18n.changeLanguage(locale);
    close();
  }
  return <div className="v2-sidebar-foot">
    <button type="button" className="v2-account" aria-haspopup="menu" aria-expanded={Boolean(anchor)} aria-label={c("accountMenu") + " · " + user.email}
      onClick={event => setAnchor(event.currentTarget)}>
      <span className="v2-avatar" aria-hidden="true">{user.email.slice(0, 1).toUpperCase()}</span>
      <span className="v2-account-email">{user.email}</span>
      {quota.data?.available != null ? <span className="v2-quota-count" title={t("v2Account.available")}>{quota.data.available}</span> : null}
    </button>
    <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={close} anchorOrigin={{ vertical: "top", horizontal: "left" }} transformOrigin={{ vertical: "bottom", horizontal: "left" }}>
      <MenuItem onClick={() => { close(); onOpenAccount("account"); }}>{c("menuAccount")}</MenuItem>
      <MenuItem aria-haspopup="menu" aria-expanded={Boolean(language)} onClick={event => setLanguage(event.currentTarget)}>
        <span style={{ flex: 1 }}>{t("common.language")}</span><IconChevronRight size={16} stroke={1.75} /></MenuItem>
      <MenuItem onClick={() => { close(); onOpenAccount("about"); }}>{c("menuAbout")}</MenuItem>
      <MenuItem onClick={() => { close(); onLogout(); }}>{t("auth.logoutMenu")}</MenuItem>
    </Menu>
    <Menu anchorEl={language} open={Boolean(language)} onClose={() => setLanguage(null)} anchorOrigin={{ vertical: "top", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "left" }}>
      {SUPPORTED_LOCALES.map(option => option.value).map(locale => <MenuItem key={locale} selected={locale === current} onClick={() => void choose(locale)}>
        <span style={{ flex: 1 }}>{t(getLocaleLabelKey(locale))}</span>{locale === current ? <IconCheck size={16} stroke={1.75} /> : null}</MenuItem>)}
    </Menu>
  </div>;
}
