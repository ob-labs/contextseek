import { useCallback, useEffect, useRef, useState } from "react";
import { History, RotateCcw, GitBranch, AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import { errorMessage } from "@/lib/utils";
import type { ConfigBlame, ConfigDiff, ConfigHistoryEntry, ConfigStatus } from "@/lib/types";

/**
 * Inline config version-history section: fetches `/config/history` +
 * `/config/status`, shows the version chain, a drift badge, an agentseek
 * ingest button, expandable diff placeholder, and a per-row rollback button.
 */
export function ConfigHistorySection() {
  const { t } = useI18n();
  const [history, setHistory] = useState<ConfigHistoryEntry[]>([]);
  const [status, setStatus] = useState<ConfigStatus | null>(null);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [diffByVersion, setDiffByVersion] = useState<Record<string, ConfigDiff>>({});
  const [targetDiffByVersion, setTargetDiffByVersion] = useState<Record<string, ConfigDiff>>({});
  const [blameByKey, setBlameByKey] = useState<Record<string, ConfigBlame | null>>({});
  const [selectedBlameKey, setSelectedBlameKey] = useState<string | null>(null);
  const [loadingDiff, setLoadingDiff] = useState<string | null>(null);
  const [loadingBlameKey, setLoadingBlameKey] = useState<string | null>(null);
  const [timelineByKey, setTimelineByKey] = useState<Record<string, string[]>>({});
  const [agentseekPath, setAgentseekPath] = useState("");
  const agentseekFileInputRef = useRef<HTMLInputElement | null>(null);
  const [restartStatus, setRestartStatus] = useState<"idle" | "required" | "restarting" | "done">("idle");
  const [ingestCheck, setIngestCheck] = useState<{
    required: string[];
    present: string[];
    missing: string[];
    ready: boolean;
  } | null>(null);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeStart = total === 0 ? 0 : page * pageSize + 1;
  const rangeEnd = total === 0 ? 0 : Math.min((page + 1) * pageSize, total);

  const refresh = useCallback(async () => {
    const offset = page * pageSize;
    const [h, s] = await Promise.all([
      ctx.getConfigHistoryPage(offset, pageSize),
      ctx.getConfigStatus(),
    ]);
    setHistory(h.items);
    setTotal(h.total);
    setStatus(s);
  }, [page, pageSize]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onRollback = useCallback(
    async (version: string) => {
      setBusy(`rollback:${version}`);
      setMsg("");
      setRestartStatus("idle");
      try {
        const rollback = await ctx.rollbackConfig(version, "dashboard rollback");
        const [h, s] = await Promise.all([
          ctx.getConfigHistoryPage(0, pageSize),
          ctx.getConfigStatus(),
        ]);
        setPage(0);
        setHistory(h.items);
        setTotal(h.total);
        setStatus(s);
        setExpanded(rollback.version_id);
        setRestartStatus(rollback.restart_required ? "required" : "idle");
        setMsg(`${t("config.rollbackSuccess")} (${rollback.version_id})`);

        const rolledBack = h.items.find((item) => item.version_id === rollback.version_id);
        if (rolledBack?.parent_version_id) {
          setLoadingDiff(rollback.version_id);
          try {
            const diff = await ctx.getConfigDiff(rolledBack.parent_version_id, rollback.version_id);
            setDiffByVersion((prev) => ({ ...prev, [rollback.version_id]: diff }));
            const rollbackTarget = rollback.rollback_target_version_id ?? version;
            const targetDiff = await ctx.getConfigDiff(rollbackTarget, rollback.version_id);
            setTargetDiffByVersion((prev) => ({ ...prev, [rollback.version_id]: targetDiff }));
          } finally {
            setLoadingDiff(null);
          }
        }
      } catch (err) {
        setMsg(errorMessage(err));
      } finally {
        setBusy("");
      }
    },
    [pageSize, t],
  );

  const onRestartService = useCallback(async () => {
    setBusy("restart");
    setRestartStatus("restarting");
    setMsg(t("settings.restart.restarting"));
    try {
      await ctx.restart();
    } catch {
      // Service can drop connection immediately when restart starts.
    }
    let online = false;
    for (let i = 0; i < 20; i += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
      try {
        const health = await ctx.health();
        if (health.status) {
          online = true;
          break;
        }
      } catch {
        // Keep polling until service comes back or timeout is reached.
      }
    }
    if (online) {
      setRestartStatus("done");
      setMsg(t("config.restartCompleted"));
      void refresh();
    } else {
      setRestartStatus("required");
      setMsg(t("settings.restartRequired"));
    }
    setBusy("");
  }, [refresh, t]);

  const onOpenVersion = useCallback(
    async (versionId: string) => {
      setBusy(`jump:${versionId}`);
      setMsg("");
      try {
        let offset = 0;
        while (true) {
          const pageData = await ctx.getConfigHistoryPage(offset, pageSize);
          const idx = pageData.items.findIndex((item) => item.version_id === versionId);
          if (idx >= 0) {
            const pageIndex = Math.floor(offset / pageSize);
            const [, s] = await Promise.all([Promise.resolve(), ctx.getConfigStatus()]);
            setPage(pageIndex);
            setHistory(pageData.items);
            setTotal(pageData.total);
            setStatus(s);
            setExpanded(versionId);
            const found = pageData.items[idx];
            if (found.parent_version_id) {
              setLoadingDiff(versionId);
              try {
                const diff = await ctx.getConfigDiff(found.parent_version_id, versionId);
                setDiffByVersion((prev) => ({ ...prev, [versionId]: diff }));
                if (found.rollback_target_version_id && !targetDiffByVersion[versionId]) {
                  const targetDiff = await ctx.getConfigDiff(found.rollback_target_version_id, versionId);
                  setTargetDiffByVersion((prev) => ({ ...prev, [versionId]: targetDiff }));
                }
              } finally {
                setLoadingDiff(null);
              }
            }
            return;
          }
          offset += pageData.items.length;
          if (offset >= pageData.total || pageData.items.length === 0) {
            setMsg(t("config.versionNotFound", { version: versionId }));
            return;
          }
        }
      } catch (err) {
        setMsg(errorMessage(err));
      } finally {
        setBusy("");
      }
    },
    [pageSize, t, targetDiffByVersion],
  );

  const onIngestAgentseek = useCallback(async () => {
    setBusy("ingest");
    setMsg("");
    try {
      const r = await ctx.ingestAgentseek(agentseekPath.trim() || undefined, true);
      await refresh();
      if (r.version_id) {
        setMsg(`${t("config.ingestAgentseek")} ✓ (${r.version_id})`);
      } else {
        setMsg(t("config.ingestNoop"));
      }
    } catch (err) {
      setMsg(errorMessage(err));
    } finally {
      setBusy("");
    }
  }, [agentseekPath, refresh, t]);

  const onCheckAgentseekIngest = useCallback(async () => {
    setBusy("ingest-check");
    setMsg("");
    try {
      const r = await ctx.checkAgentseekIngest();
      setIngestCheck(r);
      setMsg(
        r.ready
          ? t("config.ingestCheckReady")
          : t("config.ingestCheckMissingDetail", { missing: r.missing.join(", ") }),
      );
    } catch (err) {
      setMsg(errorMessage(err));
    } finally {
      setBusy("");
    }
  }, [t]);

  const onPickAgentseekFile = useCallback(() => {
    agentseekFileInputRef.current?.click();
  }, []);

  const onAgentseekFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const path = (file as File & { path?: string }).path;
      if (path) {
        setAgentseekPath(path);
        setMsg(t("config.filePicked"));
        void onCheckAgentseekIngest();
      } else {
        setMsg(t("config.filePathUnavailable"));
      }
      e.target.value = "";
    },
    [onCheckAgentseekIngest, t],
  );

  const onToggleExpanded = useCallback(
    async (version: string, parentVersion: string | null) => {
      if (expanded === version) {
        setExpanded(null);
        return;
      }
      setExpanded(version);
      if (!parentVersion || diffByVersion[version]) return;
      setLoadingDiff(version);
      try {
        const diff = await ctx.getConfigDiff(parentVersion, version);
        setDiffByVersion((prev) => ({ ...prev, [version]: diff }));
        const record = history.find((h) => h.version_id === version);
        if (record?.rollback_target_version_id && !targetDiffByVersion[version]) {
          const targetDiff = await ctx.getConfigDiff(record.rollback_target_version_id, version);
          setTargetDiffByVersion((prev) => ({ ...prev, [version]: targetDiff }));
        }
      } catch (err) {
        setMsg(errorMessage(err));
      } finally {
        setLoadingDiff(null);
      }
    },
    [diffByVersion, expanded, history, targetDiffByVersion],
  );

  const onLoadBlame = useCallback(async (key: string) => {
    setSelectedBlameKey(key);
    if (blameByKey[key] !== undefined) return;
    setLoadingBlameKey(key);
    try {
      const blame = await ctx.getConfigBlame(key);
      setBlameByKey((prev) => ({ ...prev, [key]: blame.version_id ? blame : null }));
    } catch (err) {
      setMsg(errorMessage(err));
    } finally {
      setLoadingBlameKey(null);
    }
  }, [blameByKey]);

  const fetchFullTimeline = useCallback(async (key: string): Promise<string[]> => {
    const all: ConfigHistoryEntry[] = [];
    let offset = 0;
    const limit = 50;
    while (true) {
      const pageData = await ctx.getConfigHistoryPage(offset, limit);
      all.push(...pageData.items);
      offset += pageData.items.length;
      if (offset >= pageData.total || pageData.items.length === 0) break;
    }
    const versions = await Promise.all(
      all.map(async (h) => ({
        meta: h,
        effective: await ctx.getConfigVersion(h.version_id, "effective"),
      })),
    );
    const path = key.split(".");
    const getAtPath = (obj: unknown): unknown => {
      let cur: unknown = obj;
      for (const part of path) {
        if (cur == null || typeof cur !== "object" || !(part in cur)) return undefined;
        cur = (cur as Record<string, unknown>)[part];
      }
      return cur;
    };
    const lines: string[] = [];
    for (let i = 0; i < versions.length; i += 1) {
      const cur = versions[i];
      const older = i + 1 < versions.length ? versions[i + 1] : null;
      const curVal = getAtPath(cur.effective);
      const oldVal = older ? getAtPath(older.effective) : undefined;
      if (JSON.stringify(curVal) !== JSON.stringify(oldVal)) {
        lines.push(`${cur.meta.version_id} (${cur.meta.origin}) => ${JSON.stringify(curVal)}`);
      }
    }
    return lines.length ? lines : [t("config.noChangesFound")];
  }, [t]);

  const onLoadTimeline = useCallback(
    async (key: string) => {
      setSelectedBlameKey(key);
      if (timelineByKey[key]) return;
      setLoadingBlameKey(key);
      try {
        const lines = await fetchFullTimeline(key);
        setTimelineByKey((prev) => ({ ...prev, [key]: lines }));
      } catch (err) {
        setMsg(errorMessage(err));
      } finally {
        setLoadingBlameKey(null);
      }
    },
    [fetchFullTimeline, timelineByKey],
  );

  const onCopyTimeline = useCallback(async () => {
    if (!selectedBlameKey || !timelineByKey[selectedBlameKey]) return;
    const text = [`key: ${selectedBlameKey}`, ...timelineByKey[selectedBlameKey]].join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setMsg(t("config.timelineCopied"));
    } catch {
      setMsg(t("config.copyFailed"));
    }
  }, [selectedBlameKey, t, timelineByKey]);

  const onCopySourceRef = useCallback(async () => {
    if (!selectedBlameKey) return;
    const sourceRef = blameByKey[selectedBlameKey]?.source_ref;
    if (!sourceRef) {
      setMsg(t("config.noSourceRef"));
      return;
    }
    try {
      await navigator.clipboard.writeText(sourceRef);
      setMsg(t("config.sourceRefCopied"));
    } catch {
      setMsg(t("config.copyFailed"));
    }
  }, [blameByKey, selectedBlameKey, t]);

  const buildTimelineMarkdown = useCallback((): string | null => {
    if (!selectedBlameKey || !timelineByKey[selectedBlameKey]) return null;
    const lines = timelineByKey[selectedBlameKey];
    return [
      `# Config Key Timeline`,
      ``,
      `- key: \`${selectedBlameKey}\``,
      `- entries: ${lines.length}`,
      ``,
      `## Changes`,
      ...lines.map((line) => `- ${line}`),
      ``,
    ].join("\n");
  }, [selectedBlameKey, timelineByKey]);

  const onExportTimelineMarkdown = useCallback(() => {
    const markdown = buildTimelineMarkdown();
    if (!markdown || !selectedBlameKey) return;
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `config-timeline-${selectedBlameKey.replace(/\./g, "_")}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setMsg(t("config.markdownExported"));
  }, [buildTimelineMarkdown, selectedBlameKey, t]);

  const groupedDiff = (v: ConfigDiff | undefined) => [
    { name: "added", label: t("config.diffAdded"), keys: v?.added ?? [] },
    { name: "changed", label: t("config.diffChanged"), keys: v?.changed ?? [] },
    { name: "removed", label: t("config.diffRemoved"), keys: v?.removed ?? [] },
  ];

  const renderValueDelta = (diff: ConfigDiff, group: string, key: string): string | null => {
    if (group === "changed") {
      const delta = diff.changed_values?.[key];
      if (!delta) return null;
      return `${JSON.stringify(delta.before)} -> ${JSON.stringify(delta.after)}`;
    }
    if (group === "added") {
      if (!(key in (diff.added_values ?? {}))) return null;
      return `+ ${JSON.stringify(diff.added_values?.[key])}`;
    }
    if (group === "removed") {
      if (!(key in (diff.removed_values ?? {}))) return null;
      return `- ${JSON.stringify(diff.removed_values?.[key])}`;
    }
    return null;
  };

  const originBadgeClass = (origin: string | undefined): string => {
    switch ((origin ?? "").toLowerCase()) {
      case "manual":
        return "border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-300";
      case "agentseek-projection":
        return "border-violet-300 bg-violet-50 text-violet-700 dark:border-violet-800 dark:bg-violet-950/40 dark:text-violet-300";
      case "rollback":
        return "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300";
      case "migration":
        return "border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300";
      default:
        return "border-muted-foreground/30 bg-muted text-muted-foreground";
    }
  };

  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4" />
            <span className="font-medium">{t("config.history")}</span>
            {status?.current_version && (
              <Badge variant="secondary">
                <GitBranch className="mr-1 h-3 w-3" />
                {status.current_version}
              </Badge>
            )}
            {(status?.drift?.env || status?.drift?.runtime) && (
              <Badge variant="destructive">
                <AlertTriangle className="mr-1 h-3 w-3" />
                {t("config.drift")}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={busy === "ingest-check"}
              onClick={() => void onCheckAgentseekIngest()}
            >
              {t("config.ingestCheck")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy === "ingest"}
              onClick={() => void onIngestAgentseek()}
            >
              {t("config.ingestAgentseek")}
            </Button>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <input
            ref={agentseekFileInputRef}
            type="file"
            className="hidden"
            accept=".json,.yaml,.yml,.toml,.env,text/plain,application/json"
            onChange={onAgentseekFileChange}
          />
          <input
            className="h-8 w-full rounded border border-input bg-background px-2 text-xs"
            placeholder={t("config.agentseekPathPlaceholder")}
            value={agentseekPath}
            onChange={(e) => setAgentseekPath(e.target.value)}
          />
          <Button size="sm" variant="outline" onClick={onPickAgentseekFile}>
            {t("config.pickFile")}
          </Button>
        </div>
        {ingestCheck && (
          <div className="text-xs text-muted-foreground">
            {t("config.ingestCheckStatus")}: {ingestCheck.ready ? t("config.ingestCheckReady") : t("config.ingestCheckNotReady")}
          </div>
        )}
        {restartStatus !== "idle" && (
          <div
            className={`flex items-center justify-between rounded-md px-2 py-1 text-xs ${
              restartStatus === "done"
                ? "border border-emerald-500/40 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200"
                : "border border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200"
            }`}
          >
            <span>
              {restartStatus === "done"
                ? t("config.restartCompleted")
                : restartStatus === "restarting"
                  ? t("settings.restart.restarting")
                  : t("settings.restartRequired")}
            </span>
            {restartStatus === "required" && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy === "restart"}
                onClick={() => void onRestartService()}
              >
                {t("settings.restart.confirm")}
              </Button>
            )}
          </div>
        )}
        {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <span>
              {t("config.page")} {page + 1} / {totalPages} · {t("config.total")} {total}
            </span>
            <span>
              {t("config.range")} {rangeStart}-{rangeEnd} / {total}
            </span>
            <select
              className="h-7 rounded border border-input bg-background px-2 text-xs"
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPage(0);
              }}
            >
              <option value={20}>20 / {t("config.perPage")}</option>
              <option value={50}>50 / {t("config.perPage")}</option>
              <option value={100}>100 / {t("config.perPage")}</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              disabled={page === 0}
              onClick={() => setPage(0)}
            >
              {t("config.first")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              {t("config.prev")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={(page + 1) * pageSize >= total}
              onClick={() => setPage((p) => p + 1)}
            >
              {t("config.next")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={(page + 1) * pageSize >= total}
              onClick={() => setPage(Math.max(0, totalPages - 1))}
            >
              {t("config.last")}
            </Button>
          </div>
        </div>
        <ul className="space-y-1 text-sm">
          {history.map((v) => (
            <li key={v.version_id} className="rounded border p-2">
              <div className="flex items-center justify-between">
                <div className="flex min-w-0 items-center gap-2 truncate">
                  <code>{v.version_id}</code>
                  <Badge
                    variant="outline"
                    className={`text-[10px] ${originBadgeClass(v.origin)}`}
                  >
                    {v.origin}
                  </Badge>
                  <span className="truncate text-sm text-muted-foreground">
                    {v.author} · {v.reason}
                  </span>
                  {v.rollback_target_version_id && (
                    <button
                      type="button"
                      className="rounded border px-1 py-0.5 text-[10px] text-muted-foreground hover:bg-muted"
                      disabled={busy === `jump:${v.rollback_target_version_id}`}
                      onClick={() => void onOpenVersion(v.rollback_target_version_id!)}
                      title={t("config.openVersion")}
                    >
                      {t("config.rollbackTo")} {v.rollback_target_version_id}
                    </button>
                  )}
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void onToggleExpanded(v.version_id, v.parent_version_id)}
                  >
                    {t("config.diff")}
                  </Button>
                  {v.version_id !== status?.current_version && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy === `rollback:${v.version_id}`}
                      onClick={() => void onRollback(v.version_id)}
                    >
                      <RotateCcw className="mr-1 h-3 w-3" />
                      {t("config.rollback")}
                    </Button>
                  )}
                </div>
              </div>
              {expanded === v.version_id && (
                <pre className="mt-2 max-h-40 overflow-auto rounded bg-muted p-2 text-xs text-muted-foreground">
                  {t("config.history")} · {v.version_id}
                  {"\n"}
                  {t("config.parent")}: {v.parent_version_id ?? "—"}
                  {"\n"}
                  {t("config.createdAt")}: {v.created_at}
                  {"\n"}
                  {t("config.origin")}: {v.origin} · {t("config.author")}: {v.author}
                  {v.rollback_target_version_id && (
                    <>
                      {"\n"}
                      {t("config.rollbackTarget")}: {v.rollback_target_version_id}
                    </>
                  )}
                  {v.parent_version_id && (
                    <>
                      {"\n\n"}
                      {loadingDiff === v.version_id
                        ? t("config.loadingDiff")
                        : JSON.stringify(
                            diffByVersion[v.version_id] ?? {
                              added: [],
                              changed: [],
                              removed: [],
                            },
                            null,
                            2,
                          )}
                    </>
                  )}
                </pre>
              )}
              {expanded === v.version_id && v.parent_version_id && diffByVersion[v.version_id] && (
                <div className="mt-2 space-y-2">
                  {groupedDiff(diffByVersion[v.version_id]).map((group) => (
                    <div key={group.name} className="flex flex-wrap items-center gap-1">
                      <span className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                        {group.label}
                      </span>
                      {group.keys.length === 0 && (
                        <span className="text-[10px] text-muted-foreground">{t("config.none")}</span>
                      )}
                      {group.keys.slice(0, 10).map((key) => (
                        <div key={key} className="flex flex-wrap items-center gap-1">
                          <button
                            type="button"
                            className="rounded border px-1 py-0.5 font-mono text-[10px] text-muted-foreground hover:bg-muted"
                            onClick={() => {
                              void onLoadBlame(key);
                              void onLoadTimeline(key);
                            }}
                          >
                            {key}
                          </button>
                          {renderValueDelta(diffByVersion[v.version_id], group.name, key) && (
                            <span className="rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground">
                              {renderValueDelta(diffByVersion[v.version_id], group.name, key)}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  ))}
                  {v.rollback_target_version_id && targetDiffByVersion[v.version_id] && (
                    <div className="rounded border border-emerald-500/30 bg-emerald-500/5 p-2">
                      <div className="mb-1 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
                        {t("config.targetCompare", {
                          target: v.rollback_target_version_id,
                          current: v.version_id,
                        })}
                      </div>
                      {groupedDiff(targetDiffByVersion[v.version_id]).map((group) => (
                        <div key={`target-${group.name}`} className="flex flex-wrap items-center gap-1">
                          <span className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                            {group.label}
                          </span>
                          {group.keys.length === 0 ? (
                            <span className="text-[10px] text-muted-foreground">{t("config.none")}</span>
                          ) : (
                            <span className="text-[10px] text-muted-foreground">
                              {group.keys.length}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {loadingBlameKey && <span className="text-[10px] text-muted-foreground">{t("config.loadingBlame")}</span>}
                  {selectedBlameKey && blameByKey[selectedBlameKey] && (
                    <div className="flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
                      <span>{t("config.blame")} {selectedBlameKey}</span>
                      <Badge variant="secondary" className="text-[10px]">
                        {blameByKey[selectedBlameKey]?.version_id}
                      </Badge>
                      <Badge
                        variant="outline"
                        className={`text-[10px] ${originBadgeClass(
                          blameByKey[selectedBlameKey]?.origin,
                        )}`}
                      >
                        {blameByKey[selectedBlameKey]?.origin}
                      </Badge>
                      <Badge variant="outline" className="max-w-[360px] truncate text-[10px]">
                        {t("config.sourceRef")}: {blameByKey[selectedBlameKey]?.source_ref ?? t("config.none")}
                      </Badge>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 px-2 text-[10px]"
                        onClick={() => void onCopySourceRef()}
                      >
                        {t("config.copySourceRef")}
                      </Button>
                    </div>
                  )}
                  {selectedBlameKey && timelineByKey[selectedBlameKey] && (
                    <div className="space-y-1">
                      <div className="flex items-center gap-1">
                        <Button size="sm" variant="ghost" onClick={() => void onCopyTimeline()}>
                          {t("config.copyTimeline")}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => void onExportTimelineMarkdown()}>
                          {t("config.exportTimelineMarkdown")}
                        </Button>
                      </div>
                      <pre className="max-h-32 overflow-auto rounded bg-muted p-2 text-[10px] text-muted-foreground">
                        {timelineByKey[selectedBlameKey].join("\n")}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
