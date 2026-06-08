import dagre from "@dagrejs/dagre";
import {
  Background,
  Controls,
  type Edge,
  type Node,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";

import type { EvidenceChain, Stage } from "@/lib/types";

const STAGE_COLOR: Record<Stage, string> = {
  raw: "#cbd5e1",
  extracted: "#7dd3fc",
  knowledge: "#6ee7b7",
  skill: "#c4b5fd",
};

const NODE_W = 180;
const NODE_H = 56;

function layout(chain: EvidenceChain): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 30, ranksep: 70 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of chain.nodes) {
    g.setNode(n.item_id, { width: NODE_W, height: NODE_H });
  }
  for (const e of chain.edges) {
    if (g.hasNode(e.source_id) && g.hasNode(e.target_id)) {
      g.setEdge(e.source_id, e.target_id);
    }
  }
  dagre.layout(g);

  const criticalSet = new Set(chain.critical_path);
  const conflictTargets = new Set(chain.conflicts.map((c) => c.item_id));

  const nodes: Node[] = chain.nodes.map((n) => {
    const pos = g.node(n.item_id);
    const onCritical = criticalSet.has(n.item_id);
    return {
      id: n.item_id,
      position: { x: (pos?.x ?? 0) - NODE_W / 2, y: (pos?.y ?? 0) - NODE_H / 2 },
      data: {
        label: (
          <div className="text-left">
            <div className="truncate font-mono text-[10px] opacity-70">{n.item_id}</div>
            <div className="text-xs font-medium">{n.stage}</div>
            <div className="text-[10px]">
              conf {n.effective_confidence.toFixed(2)}
              {n.is_missing && " · missing"}
            </div>
          </div>
        ),
      },
      style: {
        width: NODE_W,
        height: NODE_H,
        borderRadius: 8,
        padding: 6,
        fontSize: 12,
        background: n.is_missing ? "#fff" : STAGE_COLOR[n.stage],
        border: n.is_root
          ? "2px solid #1e293b"
          : onCritical
            ? "2px solid #f59e0b"
            : n.is_missing
              ? "2px dashed #94a3b8"
              : "1px solid #94a3b8",
        opacity: n.is_missing ? 0.6 : 1,
      },
    };
  });

  const edges: Edge[] = chain.edges.map((e, i) => {
    const isConflict = e.relation === "refuted_by" || conflictTargets.has(e.target_id);
    return {
      id: `${e.source_id}->${e.target_id}-${i}`,
      source: e.source_id,
      target: e.target_id,
      label: e.relation,
      animated: isConflict,
      style: { stroke: isConflict ? "#e11d48" : "#64748b" },
      labelStyle: { fontSize: 9, fill: "#475569" },
    };
  });

  return { nodes, edges };
}

export function EvidenceGraph({ chain }: { chain: EvidenceChain }) {
  const { nodes, edges } = useMemo(() => layout(chain), [chain]);

  if (chain.nodes.length === 0) {
    return <div className="p-6 text-sm text-muted-foreground">该条目没有证据链节点</div>;
  }

  return (
    <div className="h-[480px] w-full rounded-md border">
      <ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false}>
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
