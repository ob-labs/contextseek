import { PlusCircle } from "lucide-react";
import { useState } from "react";

import { AsyncButton } from "@/components/common/AsyncButton";
import { StageBadge } from "@/components/common/StageBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useNav } from "@/context/NavContext";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import { useScope } from "@/context/ScopeContext";
import { errorMessage } from "@/lib/utils";
import type { AddResponse } from "@/lib/types";

function tagsFromInput(value: string): string[] {
  return value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function tagsFromJson(value: unknown): string[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const raw = (value as Record<string, unknown>).tags;
  if (Array.isArray(raw)) {
    return raw
      .map((tag) => (typeof tag === "string" ? tag.trim() : String(tag).trim()))
      .filter(Boolean);
  }
  if (typeof raw === "string") return tagsFromInput(raw);
  return [];
}

function mergeTags(...groups: string[][]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const group of groups) {
    for (const tag of group) {
      const key = tag.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      result.push(tag);
    }
  }
  return result;
}

export function WritePanel() {
  const { t } = useI18n();
  const { scope } = useScope();
  const { navigate } = useNav();
  const [content, setContent] = useState("");
  const [asJson, setAsJson] = useState(false);
  const [source, setSource] = useState("api");
  const [tags, setTags] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<AddResponse | null>(null);

  const submit = async () => {
    setError(null);
    setResult(null);
    let payload: unknown = content;
    let jsonTags: string[] = [];
    if (asJson) {
      try {
        payload = JSON.parse(content);
        jsonTags = tagsFromJson(payload);
      } catch {
        setError(new Error(t("write.jsonInvalid")));
        return;
      }
    }
    const resolvedTags = mergeTags(tagsFromInput(tags), jsonTags);
    setLoading(true);
    try {
      const res = await ctx.add({
        scope,
        content: payload,
        source: source || "api",
        tags: resolvedTags,
      });
      setResult(res);
      setContent("");
      setTags("");
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-6">
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label htmlFor="content">{t("write.content")}</Label>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={asJson}
                  onChange={(e) => setAsJson(e.target.checked)}
                />
                {t("write.asJson")}
              </label>
            </div>
            <Textarea
              id="content"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={asJson ? t("write.contentJsonPlaceholder") : t("write.contentTextPlaceholder")}
              className="min-h-32"
            />
          </div>
          <div className="flex flex-wrap gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="source">source</Label>
              <Input
                id="source"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                className="w-40"
              />
            </div>
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="tags">{t("write.tags")}</Label>
              <Input
                id="tags"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder={t("write.tagsPlaceholder")}
              />
            </div>
          </div>
          <AsyncButton loading={loading} onClick={submit} disabled={!content.trim()}>
            <PlusCircle className="h-4 w-4" /> {t("write.action", { scope })}
          </AsyncButton>
          {error ? <p className="text-sm text-destructive">{errorMessage(error)}</p> : null}
        </CardContent>
      </Card>

      {result && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 pt-6">
            <span className="text-sm">{t("write.written")}</span>
            <span className="font-mono text-sm">{result.id}</span>
            <StageBadge stage={result.stage} />
            <Button variant="outline" size="sm" onClick={() => navigate("browse")}>
              {t("write.goBrowse")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate("provenance", { itemId: result.id })}
            >
              {t("write.goProvenance")}
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
