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
import { useScope } from "@/context/ScopeContext";
import { errorMessage } from "@/lib/utils";
import type { AddResponse } from "@/lib/types";

export function WritePanel() {
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
    if (asJson) {
      try {
        payload = JSON.parse(content);
      } catch {
        setError(new Error("content 不是合法 JSON"));
        return;
      }
    }
    setLoading(true);
    try {
      const res = await ctx.add({
        scope,
        content: payload,
        source: source || "api",
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
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
              <Label htmlFor="content">内容</Label>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={asJson}
                  onChange={(e) => setAsJson(e.target.checked)}
                />
                按 JSON 解析
              </label>
            </div>
            <Textarea
              id="content"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={asJson ? '{"key": "value"}' : "要写入的文本…"}
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
              <Label htmlFor="tags">tags (逗号分隔)</Label>
              <Input
                id="tags"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="note, draft"
              />
            </div>
          </div>
          <AsyncButton loading={loading} onClick={submit} disabled={!content.trim()}>
            <PlusCircle className="h-4 w-4" /> 写入 scope「{scope}」
          </AsyncButton>
          {error ? <p className="text-sm text-destructive">{errorMessage(error)}</p> : null}
        </CardContent>
      </Card>

      {result && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 pt-6">
            <span className="text-sm">已写入：</span>
            <span className="font-mono text-sm">{result.id}</span>
            <StageBadge stage={result.stage} />
            <Button variant="outline" size="sm" onClick={() => navigate("browse")}>
              去浏览
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate("provenance", { itemId: result.id })}
            >
              看溯源
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
