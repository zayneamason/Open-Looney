import type { DocNode, NodeType } from "./types";

export const NODE_TYPE_LABEL: Record<NodeType, string> = {
  document: "DOC",
  section: "SEC",
  paragraph: "P",
  sentence: "S",
  list: "LIST",
  list_item: "LI",
  figure: "FIG",
  table: "TABLE",
  row: "ROW",
  cell: "CELL",
};

export const NODE_TYPE_COLOR: Record<NodeType, string> = {
  document: "text-purple-700",
  section: "text-blue-700",
  paragraph: "text-gray-700",
  sentence: "text-gray-400",
  list: "text-emerald-700",
  list_item: "text-emerald-600",
  figure: "text-amber-700",
  table: "text-cyan-700",
  row: "text-cyan-600",
  cell: "text-cyan-500",
};

export function metaString(node: DocNode, key: string): string | null {
  const value = node.meta_json?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function metaNumber(node: DocNode, key: string): number | null {
  const value = node.meta_json?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function pageBadge(node: DocNode): string | null {
  const pn = metaNumber(node, "page_num");
  return typeof pn === "number" ? `p.${pn}` : null;
}

export function nodePreview(node: DocNode): string {
  const metaTitle = metaString(node, "title");
  if (metaTitle) return metaTitle;

  const content = node.content.trim();
  if (content) return content.length > 100 ? `${content.slice(0, 100)}...` : content;

  const pageNum = metaNumber(node, "page_num");
  if (node.node_type === "document") return "Document";
  if (node.node_type === "section" && pageNum !== null) return `Page ${pageNum}`;
  if (node.node_type === "section") return `Section ${node.position + 1}`;
  if (node.node_type === "paragraph") return "Paragraph";
  if (node.node_type === "list") return "List";
  if (node.node_type === "table") return "Table";
  if (node.node_type === "row") return `Row ${node.position + 1}`;
  if (node.node_type === "cell") return `Cell ${node.position + 1}`;
  if (node.node_type === "figure") return "Figure";
  if (node.node_type === "list_item") return `Item ${node.position + 1}`;
  return "(empty)";
}

export function isLeafNodeType(type: NodeType): boolean {
  return type === "sentence" || type === "cell" || type === "figure" || type === "list_item";
}

