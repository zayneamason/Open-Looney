import { useEffect } from "react";
import { useReader } from "../store";
import type { DocNode } from "../types";
import {
  NODE_TYPE_COLOR,
  NODE_TYPE_LABEL,
  isLeafNodeType,
  nodePreview,
  pageBadge,
} from "../nodeDisplay";

function TreeNode({ node, depth }: { node: DocNode; depth: number }) {
  const treeExpansion = useReader((s) => s.treeExpansion);
  const childrenByParent = useReader((s) => s.childrenByParent);
  const selectedNode = useReader((s) => s.selectedNode);
  const toggleExpand = useReader((s) => s.toggleExpand);
  const selectNode = useReader((s) => s.selectNode);

  const isExpanded = treeExpansion.has(node.ulid);
  const isLeaf = isLeafNodeType(node.node_type);
  const isSelected = selectedNode?.ulid === node.ulid;
  const children = childrenByParent[node.ulid];
  const page = pageBadge(node);

  return (
    <div>
      <div
        className={`flex items-start gap-1.5 py-0.5 pr-2 cursor-pointer hover:bg-gray-100 ${
          isSelected ? "bg-blue-100 hover:bg-blue-100" : ""
        }`}
        style={{ paddingLeft: `${depth * 12 + 6}px` }}
        onClick={() => void selectNode(node.ulid)}
      >
        {!isLeaf ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              void toggleExpand(node.ulid);
            }}
            className="text-gray-400 hover:text-gray-700 w-3 shrink-0 text-[10px] mt-[3px]"
            aria-label={isExpanded ? "Collapse" : "Expand"}
          >
            {isExpanded ? "▾" : "▸"}
          </button>
        ) : (
          <span className="w-3 shrink-0" />
        )}
        <span
          className={`text-[9px] font-mono shrink-0 mt-[2px] ${NODE_TYPE_COLOR[node.node_type]}`}
          title={node.node_type}
        >
          {NODE_TYPE_LABEL[node.node_type]}
        </span>
        <span className="text-xs truncate flex-1 min-w-0 leading-snug">
          {nodePreview(node)}
        </span>
        {page && (
          <span className="text-[10px] text-gray-400 shrink-0 mt-[2px]">{page}</span>
        )}
      </div>
      {isExpanded && children && (
        <div>
          {children.map((child) => (
            <TreeNode key={child.ulid} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
      {isExpanded && !children && (
        <div
          className="text-[10px] text-gray-400 italic py-0.5"
          style={{ paddingLeft: `${(depth + 1) * 12 + 6}px` }}
        >
          loading…
        </div>
      )}
    </div>
  );
}

export function DocTree() {
  const rootNodes = useReader((s) => s.rootNodes);
  const loadRootNodes = useReader((s) => s.loadRootNodes);

  useEffect(() => {
    if (rootNodes === null) {
      void loadRootNodes();
    }
  }, [rootNodes, loadRootNodes]);

  if (rootNodes === null) {
    return <div className="p-4 text-xs text-gray-500">Loading…</div>;
  }
  if (rootNodes.length === 0) {
    return <div className="p-4 text-xs text-gray-500">Empty cartridge.</div>;
  }

  return (
    <div className="text-xs overflow-y-auto h-full py-1">
      {rootNodes.map((node) => (
        <TreeNode key={node.ulid} node={node} depth={0} />
      ))}
    </div>
  );
}
