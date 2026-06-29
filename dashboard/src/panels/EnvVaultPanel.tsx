import {
  Download,
  Eye,
  EyeOff,
  FileSearch,
  FolderOpen,
  KeyRound,
  Plus,
  Save,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AsyncButton } from "@/components/common/AsyncButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import {
  isNativeDialogAvailable,
  openDirectory,
  saveFile,
} from "@/lib/tauriDialog";
import type { EnvTemplateCandidate, EnvTemplateKey } from "@/lib/types";
import { errorMessage } from "@/lib/utils";

interface VaultRow {
  key: string;
  value: string;
  is_secret: boolean;
}

type KeySource = "vault" | "template" | "missing";
type GenFilter = "all" | KeySource;

// Where a template key's prefilled value comes from: vault > template default > none.
function keySource(k: EnvTemplateKey): KeySource {
  if (!k.is_missing) return "vault";
  return k.default.trim() ? "template" : "missing";
}

function deriveOutputPath(templatePath: string): string {
  const sep = templatePath.includes("\\") ? "\\" : "/";
  const idx = templatePath.lastIndexOf(sep);
  const dir = idx >= 0 ? templatePath.slice(0, idx) : "";
  return dir ? `${dir}${sep}.env` : ".env";
}

export function EnvVaultPanel() {
  const { t } = useI18n();
  const nativeDialog = isNativeDialogAvailable();

  // --- Vault state ---
  const [rows, setRows] = useState<VaultRow[]>([]);
  const [revealed, setRevealed] = useState<Set<string>>(new Set());
  const [vaultError, setVaultError] = useState<unknown>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [seededN, setSeededN] = useState<number | null>(null);
  const [seededRemoved, setSeededRemoved] = useState<number>(0);
  const [saving, setSaving] = useState(false);
  const [seeding, setSeeding] = useState(false);

  const loadVault = useCallback(async () => {
    setVaultError(null);
    try {
      const res = await ctx.envVault.items();
      setRows(res.items.map((i) => ({ ...i })));
    } catch (err) {
      setVaultError(err);
    }
  }, []);

  useEffect(() => {
    void loadVault();
  }, [loadVault]);

  const toggleReveal = (id: string) => {
    setRevealed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const updateRow = (idx: number, patch: Partial<VaultRow>) => {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };

  const addRow = () => {
    setRows((prev) => [...prev, { key: "", value: "", is_secret: false }]);
  };

  const removeRow = async (idx: number) => {
    const row = rows[idx];
    setRows((prev) => prev.filter((_, i) => i !== idx));
    if (row.key.trim()) {
      try {
        await ctx.envVault.remove(row.key.trim());
      } catch (err) {
        setVaultError(err);
      }
    }
  };

  const saveVault = async () => {
    setVaultError(null);
    setSaving(true);
    try {
      const items = rows
        .filter((r) => r.key.trim())
        .map((r) => ({ key: r.key.trim(), value: r.value }));
      await ctx.envVault.upsert({ items });
      setSavedAt(Date.now());
      await loadVault();
    } catch (err) {
      setVaultError(err);
    } finally {
      setSaving(false);
    }
  };

  const seed = async () => {
    setVaultError(null);
    setSeeding(true);
    try {
      const res = await ctx.envVault.seedContextseek();
      setSeededN(res.added);
      setSeededRemoved(res.removed);
      await loadVault();
    } catch (err) {
      setVaultError(err);
    } finally {
      setSeeding(false);
    }
  };

  // --- Generate state ---
  const [templatePath, setTemplatePath] = useState("");
  const [outputPath, setOutputPath] = useState("");
  const [keys, setKeys] = useState<EnvTemplateKey[] | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [genRevealed, setGenRevealed] = useState<Set<string>>(new Set());
  const [genError, setGenError] = useState<unknown>(null);
  const [genDone, setGenDone] = useState<string | null>(null);
  const [parsing, setParsing] = useState(false);
  const [scanningTemplates, setScanningTemplates] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [overwriteAsk, setOverwriteAsk] = useState(false);
  const [candidateAsk, setCandidateAsk] = useState(false);
  const [templateCandidates, setTemplateCandidates] = useState<EnvTemplateCandidate[]>([]);
  const [genFilter, setGenFilter] = useState<GenFilter>("all");

  const parseTemplatePath = async (path: string) => {
    const nextTemplatePath = path.trim();
    if (!nextTemplatePath) return;
    setTemplatePath(nextTemplatePath);
    setGenError(null);
    setGenDone(null);
    setKeys(null);
    setValues({});
    setCandidateAsk(false);
    setParsing(true);
    try {
      const res = await ctx.envVault.parseTemplate(nextTemplatePath);
      setKeys(res.keys);
      // Backend resolves a directory to its .env.example; reflect the actual
      // file path so the output path derives from the right directory.
      const resolvedTemplate = res.template_path || nextTemplatePath;
      setTemplatePath(resolvedTemplate);
      const nextValues: Record<string, string> = {};
      for (const k of res.keys) {
        // Vault value wins; otherwise fall back to the template's own default.
        nextValues[k.key] = k.is_missing ? k.default : k.value_from_vault;
      }
      setValues(nextValues);
      setGenFilter("all");
      setOutputPath(deriveOutputPath(resolvedTemplate));
    } catch (err) {
      setGenError(err);
    } finally {
      setParsing(false);
    }
  };

  const selectTemplateCandidate = async (candidate: EnvTemplateCandidate) => {
    setCandidateAsk(false);
    await parseTemplatePath(candidate.path);
  };

  const scanTemplateRoot = async (path: string) => {
    const nextPath = path.trim();
    if (!nextPath) return;
    setTemplatePath(nextPath);
    setGenError(null);
    setGenDone(null);
    setKeys(null);
    setValues({});
    setTemplateCandidates([]);
    setCandidateAsk(false);
    setScanningTemplates(true);
    try {
      const res = await ctx.envVault.listTemplates(nextPath);
      setTemplatePath(res.root_path || nextPath);
      if (res.templates.length === 1) {
        await parseTemplatePath(res.templates[0].path);
        return;
      }
      if (res.templates.length > 1) {
        setTemplateCandidates(res.templates);
        setCandidateAsk(true);
        return;
      }
      setGenError(new Error(t("envVault.gen.noEnvTemplates")));
    } catch (err) {
      setGenError(err);
    } finally {
      setScanningTemplates(false);
    }
  };

  const pickTemplateFolder = async () => {
    setGenError(null);
    try {
      const picked = await openDirectory({
        title: t("envVault.gen.pickTemplate"),
      });
      if (picked) await scanTemplateRoot(picked);
    } catch (err) {
      setGenError(err);
    }
  };

  const pickOutput = async () => {
    setGenError(null);
    try {
      const picked = await saveFile({
        title: t("envVault.gen.pickOutput"),
        defaultPath: outputPath || (templatePath ? deriveOutputPath(templatePath) : undefined),
      });
      if (picked) setOutputPath(picked);
    } catch (err) {
      setGenError(err);
    }
  };

  const parse = async () => {
    await scanTemplateRoot(templatePath);
  };

  const toggleGenReveal = (key: string) => {
    setGenRevealed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const runGenerate = async (overwrite: boolean) => {
    if (!keys || !outputPath.trim()) return;
    setGenError(null);
    setGenDone(null);
    setGenerating(true);
    try {
      const res = await ctx.envVault.generate({
        template_path: templatePath.trim(),
        output_path: outputPath.trim(),
        values,
        overwrite,
      });
      if (res.status === "exists") {
        setOverwriteAsk(true);
        return;
      }
      setGenDone(
        t("envVault.gen.done", {
          path: res.output_path,
          keys: res.written_keys ?? 0,
          synced: res.synced_to_vault ?? 0,
        }),
      );
      await loadVault();
    } catch (err) {
      setGenError(err);
    } finally {
      setGenerating(false);
    }
  };

  const generate = () => runGenerate(false);

  const confirmOverwrite = async () => {
    setOverwriteAsk(false);
    await runGenerate(true);
  };

  const sourceCounts = useMemo(() => {
    const counts = { vault: 0, template: 0, missing: 0 };
    for (const k of keys ?? []) counts[keySource(k)] += 1;
    return counts;
  }, [keys]);

  const visibleKeys = useMemo(
    () =>
      (keys ?? []).filter(
        (k) => genFilter === "all" || keySource(k) === genFilter,
      ),
    [keys, genFilter],
  );

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      {/* Vault KV editor */}
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1">
              <h2 className="flex items-center gap-2 text-base font-semibold">
                <KeyRound className="h-4 w-4" /> {t("envVault.vault.title")}
              </h2>
              <p className="text-xs text-muted-foreground">
                {t("envVault.vault.desc")}
              </p>
            </div>
            <AsyncButton
              variant="outline"
              size="sm"
              loading={seeding}
              onClick={seed}
            >
              <Sparkles className="h-4 w-4" />
              {seeding ? t("envVault.vault.seeding") : t("envVault.vault.seed")}
            </AsyncButton>
          </div>

          {seededN !== null ? (
            <p className="text-xs text-muted-foreground">
              {t("envVault.vault.seeded", { n: seededN, removed: seededRemoved })}
            </p>
          ) : null}

          {rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("envVault.vault.empty")}
            </p>
          ) : (
            <div className="space-y-2">
              {rows.map((row, idx) => {
                const id = `${idx}:${row.key}`;
                const isRevealed = revealed.has(id);
                const masked = row.is_secret && !isRevealed;
                return (
                  <div key={id} className="flex items-center gap-2">
                    <Input
                      value={row.key}
                      onChange={(e) =>
                        updateRow(idx, {
                          key: e.target.value,
                          is_secret: /KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL|PRIVATE/i.test(
                            e.target.value,
                          ),
                        })
                      }
                      placeholder={t("envVault.vault.key")}
                      className="w-1/3 font-mono text-xs"
                    />
                    <Input
                      type={masked ? "password" : "text"}
                      value={row.value}
                      onChange={(e) => updateRow(idx, { value: e.target.value })}
                      placeholder={t("envVault.vault.value")}
                      className="flex-1 font-mono text-xs"
                    />
                    {row.is_secret ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        title={isRevealed ? t("envVault.vault.hide") : t("envVault.vault.reveal")}
                        onClick={() => toggleReveal(id)}
                      >
                        {isRevealed ? (
                          <EyeOff className="h-4 w-4" />
                        ) : (
                          <Eye className="h-4 w-4" />
                        )}
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      title={t("envVault.vault.delete")}
                      onClick={() => void removeRow(idx)}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  </div>
                );
              })}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={addRow}>
              <Plus className="h-4 w-4" /> {t("envVault.vault.add")}
            </Button>
            <AsyncButton size="sm" loading={saving} onClick={saveVault}>
              <Save className="h-4 w-4" />
              {saving ? t("envVault.vault.saving") : t("envVault.vault.save")}
            </AsyncButton>
            {savedAt !== null && !saving ? (
              <span className="text-xs text-muted-foreground">
                {t("envVault.vault.saved")}
              </span>
            ) : null}
          </div>

          {vaultError ? (
            <p className="text-sm text-destructive">{errorMessage(vaultError)}</p>
          ) : null}
        </CardContent>
      </Card>

      {/* Generate .env */}
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="space-y-1">
            <h2 className="flex items-center gap-2 text-base font-semibold">
              <Download className="h-4 w-4" /> {t("envVault.gen.title")}
            </h2>
            <p className="text-xs text-muted-foreground">{t("envVault.gen.desc")}</p>
          </div>

          <div className="space-y-1.5">
            <Label>{t("envVault.gen.template")}</Label>
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={templatePath}
                onChange={(e) => setTemplatePath(e.target.value)}
                placeholder={t("envVault.gen.templatePlaceholder")}
                className="min-w-[16rem] flex-1 font-mono text-xs"
              />
              {nativeDialog ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void pickTemplateFolder()}
                >
                  <FolderOpen className="h-4 w-4" />
                  {t("envVault.gen.pickTemplate")}
                </Button>
              ) : null}
              <AsyncButton
                size="sm"
                loading={parsing || scanningTemplates}
                onClick={parse}
                disabled={!templatePath.trim()}
              >
                <FileSearch className="h-4 w-4" />
                {parsing || scanningTemplates
                  ? t("envVault.gen.parsing")
                  : t("envVault.gen.parse")}
              </AsyncButton>
            </div>
            {!nativeDialog ? (
              <p className="text-xs text-muted-foreground">{t("envVault.gen.manualHint")}</p>
            ) : null}
          </div>

          {keys ? (
            <>
              <div className="flex flex-wrap items-center gap-1.5">
                {(
                  [
                    ["all", t("envVault.gen.filterAll"), keys.length],
                    ["vault", t("envVault.gen.fromVault"), sourceCounts.vault],
                    ["template", t("envVault.gen.fromTemplate"), sourceCounts.template],
                    ["missing", t("envVault.gen.missing"), sourceCounts.missing],
                  ] as [GenFilter, string, number][]
                ).map(([id, label, count]) => (
                  <Button
                    key={id}
                    type="button"
                    variant={genFilter === id ? "default" : "outline"}
                    size="sm"
                    onClick={() => setGenFilter(id)}
                  >
                    {label} ({count})
                  </Button>
                ))}
              </div>

              <div className="space-y-2">
                {visibleKeys.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{t("common.empty")}</p>
                ) : null}
                {visibleKeys.map((k) => {
                  const isRevealed = genRevealed.has(k.key);
                  const masked = k.is_secret && !isRevealed;
                  const filled = Boolean(values[k.key]?.trim());
                  const source = keySource(k);
                  return (
                    <div key={k.key} className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="w-1/3 truncate font-mono text-xs" title={k.key}>
                        {k.key}
                      </span>
                      <Input
                        type={masked ? "password" : "text"}
                        value={values[k.key] ?? ""}
                        onChange={(e) =>
                          setValues((prev) => ({ ...prev, [k.key]: e.target.value }))
                        }
                        className="flex-1 font-mono text-xs"
                      />
                      {k.is_secret ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => toggleGenReveal(k.key)}
                          title={isRevealed ? t("envVault.vault.hide") : t("envVault.vault.reveal")}
                        >
                          {isRevealed ? (
                            <EyeOff className="h-4 w-4" />
                          ) : (
                            <Eye className="h-4 w-4" />
                          )}
                        </Button>
                      ) : null}
                      <Badge
                        variant={
                          source === "vault"
                            ? "secondary"
                            : source === "template"
                              ? "outline"
                              : filled
                                ? "outline"
                                : "destructive"
                        }
                        className="shrink-0"
                      >
                        {source === "vault"
                          ? t("envVault.gen.fromVault")
                          : source === "template"
                            ? t("envVault.gen.fromTemplate")
                            : t("envVault.gen.missing")}
                      </Badge>
                    </div>
                    {k.comment ? (
                      <p className="whitespace-pre-line pl-1 text-xs text-muted-foreground">
                        {k.comment}
                      </p>
                    ) : null}
                    </div>
                  );
                })}
              </div>

              <div className="sticky bottom-0 -mx-6 border-t bg-card/95 px-6 py-3 backdrop-blur supports-[backdrop-filter]:bg-card/80">
                <div className="flex items-end gap-2">
                  <div className="flex-1 space-y-1.5">
                    <Label>{t("envVault.gen.output")}</Label>
                    <Input
                      value={outputPath}
                      onChange={(e) => setOutputPath(e.target.value)}
                      className="font-mono text-xs"
                    />
                  </div>
                  {nativeDialog ? (
                    <Button variant="outline" size="sm" onClick={() => void pickOutput()}>
                      <FolderOpen className="h-4 w-4" /> {t("envVault.gen.pickOutput")}
                    </Button>
                  ) : null}
                  <AsyncButton
                    loading={generating}
                    onClick={generate}
                    disabled={!outputPath.trim()}
                  >
                    <Download className="h-4 w-4" />
                    {generating ? t("envVault.gen.generating") : t("envVault.gen.generate")}
                  </AsyncButton>
                </div>
                {genDone ? (
                  <p className="mt-2 text-sm text-emerald-600">{genDone}</p>
                ) : null}
                {genError ? (
                  <p className="mt-2 text-sm text-destructive">{errorMessage(genError)}</p>
                ) : null}
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">{t("envVault.gen.noTemplate")}</p>
          )}

          {!keys && genError ? (
            <p className="text-sm text-destructive">{errorMessage(genError)}</p>
          ) : null}
        </CardContent>
      </Card>

      <Dialog open={overwriteAsk} onOpenChange={setOverwriteAsk}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("envVault.gen.overwriteTitle")}</DialogTitle>
            <DialogDescription>
              {t("envVault.gen.overwriteDesc", { path: outputPath })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOverwriteAsk(false)}>
              {t("common.cancel")}
            </Button>
            <Button variant="destructive" onClick={() => void confirmOverwrite()}>
              {t("envVault.gen.overwriteConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={candidateAsk} onOpenChange={setCandidateAsk}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("envVault.gen.candidatesTitle")}</DialogTitle>
            <DialogDescription>
              {t("envVault.gen.candidatesDesc", {
                n: templateCandidates.length,
              })}
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-80 space-y-2 overflow-y-auto">
            {templateCandidates.map((candidate) => (
              <button
                key={candidate.path}
                type="button"
                className="w-full rounded-md border p-3 text-left transition hover:bg-muted"
                onClick={() => void selectTemplateCandidate(candidate)}
              >
                <span className="block font-mono text-sm font-medium">
                  {candidate.name}
                </span>
                <span className="mt-1 block truncate font-mono text-xs text-muted-foreground">
                  {candidate.path}
                </span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  {t("envVault.gen.candidateKeys", {
                    n: candidate.key_count,
                  })}
                </span>
              </button>
            ))}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCandidateAsk(false)}>
              {t("common.cancel")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
