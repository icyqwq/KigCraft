import { IconLanguage } from "@tabler/icons-react";
import { useId, useState, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import { normalizeLocale, SUPPORTED_LOCALES, writeStoredLocale, type AppLocale } from "../../i18n/locales";
import { Button, Menu, MenuItem } from "../../ui/mui";

export function LanguageSelector({ compact = false }: { compact?: boolean }) {
  const { i18n, t } = useTranslation();
  const menuId = useId();
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const isOpen = Boolean(anchorEl);
  const currentLocale = normalizeLocale(i18n.language);
  const currentLabel =
    SUPPORTED_LOCALES.find((locale) => locale.value === currentLocale)?.label ?? SUPPORTED_LOCALES[0].label;
  const ariaLabel = `${t("common.language")} ${currentLabel}`;

  async function selectLocale(locale: AppLocale) {
    writeStoredLocale(locale);
    await i18n.changeLanguage(locale);
    setAnchorEl(null);
  }

  return (
    <>
      <Button
        aria-controls={isOpen ? menuId : undefined}
        aria-expanded={isOpen}
        aria-haspopup="menu"
        aria-label={ariaLabel}
        data-testid="header-language-button"
        h={40}
        leftSection={<IconLanguage size={16} />}
        miw={compact ? 40 : 124}
        onClick={(event: MouseEvent<HTMLButtonElement>) => setAnchorEl(event.currentTarget)}
        px={compact ? 0 : undefined}
        size="sm"
        variant="light"
      >
        {currentLabel}
      </Button>
      <Menu
        anchorEl={anchorEl}
        open={isOpen}
        onClose={() => setAnchorEl(null)}
        slotProps={{ list: { id: menuId } }}
      >
        {SUPPORTED_LOCALES.map((locale) => (
          <MenuItem
            key={locale.value}
            selected={locale.value === currentLocale}
            onClick={() => void selectLocale(locale.value)}
          >
            {locale.label}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
