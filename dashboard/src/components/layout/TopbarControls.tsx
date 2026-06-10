import { Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useTheme } from "@/context/ThemeContext";
import { LANG_OPTIONS, useI18n, type Lang } from "@/lib/i18n";

/** Global language + theme switchers for the topbar's right side. */
export function TopbarControls() {
  const { lang, setLang, t } = useI18n();
  const { theme, toggle } = useTheme();

  return (
    <div className="flex items-center gap-1">
      <Select value={lang} onValueChange={(value) => setLang(value as Lang)}>
        <SelectTrigger
          className="h-8 w-auto gap-1 border-0 bg-transparent px-2 text-xs shadow-none hover:bg-accent focus:ring-0"
          title={t("topbar.lang")}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent align="end">
          {LANG_OPTIONS.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8"
        title={theme === "dark" ? t("topbar.theme.toLight") : t("topbar.theme.toDark")}
        onClick={toggle}
      >
        {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </Button>
    </div>
  );
}
