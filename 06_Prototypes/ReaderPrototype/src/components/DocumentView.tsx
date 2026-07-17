import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { errorToText, useReader } from "../store";
import type { DocNode } from "../types";
import { metaNumber, metaString, nodePreview } from "../nodeDisplay";

type ChildrenByParent = Record<string, DocNode[]>;

function groupChildren(nodes: DocNode[]): { roots: DocNode[]; childrenByParent: ChildrenByParent } {
  const roots: DocNode[] = [];
  const childrenByParent: ChildrenByParent = {};

  for (const node of nodes) {
    if (node.parent_ulid === null) {
      roots.push(node);
    } else {
      const bucket = childrenByParent[node.parent_ulid] ?? [];
      bucket.push(node);
      childrenByParent[node.parent_ulid] = bucket;
    }
  }

  const byPosition = (a: DocNode, b: DocNode) =>
    a.position - b.position || a.ulid.localeCompare(b.ulid);
  roots.sort(byPosition);
  for (const children of Object.values(childrenByParent)) {
    children.sort(byPosition);
  }

  return { roots, childrenByParent };
}

function textForNode(node: DocNode, childrenByParent: ChildrenByParent): string {
  const content = node.content.trim();
  if (content) return content;
  const children = childrenByParent[node.ulid] ?? [];
  return children
    .map((child) => textForNode(child, childrenByParent))
    .filter(Boolean)
    .join(" ");
}

function headingClass(level: number): string {
  if (level <= 1) return "text-3xl font-semibold tracking-normal text-gray-950 mt-0 mb-6";
  if (level === 2) return "text-2xl font-semibold tracking-normal text-gray-950 mt-10 mb-4";
  if (level === 3) return "text-xl font-semibold tracking-normal text-gray-900 mt-8 mb-3";
  if (level === 4) return "text-lg font-semibold tracking-normal text-gray-900 mt-6 mb-2";
  return "text-base font-semibold tracking-normal text-gray-900 mt-5 mb-2";
}

function Heading({ level, children }: { level: number; children: string }) {
  const className = headingClass(level);
  if (level <= 1) return <h1 className={className}>{children}</h1>;
  if (level === 2) return <h2 className={className}>{children}</h2>;
  if (level === 3) return <h3 className={className}>{children}</h3>;
  if (level === 4) return <h4 className={className}>{children}</h4>;
  if (level === 5) return <h5 className={className}>{children}</h5>;
  return <h6 className={className}>{children}</h6>;
}

function renderInlineText(text: string) {
  return text || <span className="text-gray-400 italic">(empty)</span>;
}

function RenderNode({
  node,
  childrenByParent,
  depth,
}: {
  node: DocNode;
  childrenByParent: ChildrenByParent;
  depth: number;
}) {
  const children = childrenByParent[node.ulid] ?? [];

  switch (node.node_type) {
    case "document":
      return (
        <>
          {children.map((child) => (
            <RenderNode key={child.ulid} node={child} childrenByParent={childrenByParent} depth={depth} />
          ))}
        </>
      );

    case "section": {
      const title = node.content.trim() || metaString(node, "title");
      const pageNum = metaNumber(node, "page_num");
      const parsedLevel = metaNumber(node, "level");
      const headingLevel = Math.max(2, Math.min(6, parsedLevel ?? depth + 2));

      if (!title && pageNum !== null) {
        return (
          <section className="mt-10 pt-5 border-t border-gray-200 first:mt-0 first:border-t-0">
            <div className="mb-4 text-[11px] font-mono uppercase tracking-wide text-gray-400">
              Page {pageNum}
            </div>
            {children.map((child) => (
              <RenderNode
                key={child.ulid}
                node={child}
                childrenByParent={childrenByParent}
                depth={depth + 1}
              />
            ))}
          </section>
        );
      }

      return (
        <section>
          <Heading level={headingLevel}>{title || `Section ${node.position + 1}`}</Heading>
          {children.map((child) => (
            <RenderNode
              key={child.ulid}
              node={child}
              childrenByParent={childrenByParent}
              depth={depth + 1}
            />
          ))}
        </section>
      );
    }

    case "paragraph": {
      const text = textForNode(node, childrenByParent);
      const isBlockquote = node.meta_json?.blockquote === true;
      if (isBlockquote) {
        return (
          <blockquote className="my-5 border-l-4 border-gray-300 pl-4 text-sm leading-7 text-gray-700">
            {renderInlineText(text)}
          </blockquote>
        );
      }
      return <p className="my-4 text-sm leading-7 text-gray-900">{renderInlineText(text)}</p>;
    }

    case "sentence":
      return <p className="my-4 text-sm leading-7 text-gray-900">{renderInlineText(node.content.trim())}</p>;

    case "list":
      return (
        <ul className="my-4 list-disc pl-6 text-sm leading-7 text-gray-900">
          {children.map((child) => (
            <RenderNode key={child.ulid} node={child} childrenByParent={childrenByParent} depth={depth + 1} />
          ))}
        </ul>
      );

    case "list_item":
      return <li>{renderInlineText(textForNode(node, childrenByParent))}</li>;

    case "figure": {
      const src = metaString(node, "src");
      const caption = node.content.trim();
      return (
        <figure className="my-6">
          {src && (
            <img
              src={src}
              alt={caption}
              className="max-w-full rounded border border-gray-200 bg-gray-50"
            />
          )}
          {(caption || src) && (
            <figcaption className="mt-2 text-xs leading-5 text-gray-500">
              {caption || src}
            </figcaption>
          )}
        </figure>
      );
    }

    case "table": {
      const rows = children.filter((child) => child.node_type === "row");
      return (
        <div className="my-6 overflow-x-auto">
          <table className="min-w-full border-collapse text-sm">
            <tbody>
              {rows.map((row) => (
                <RenderNode key={row.ulid} node={row} childrenByParent={childrenByParent} depth={depth + 1} />
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    case "row": {
      const cells = children.filter((child) => child.node_type === "cell");
      const isHeader = node.meta_json?.header === true;
      return (
        <tr>
          {cells.map((cell) => {
            const text = textForNode(cell, childrenByParent);
            return isHeader ? (
              <th key={cell.ulid} className="border border-gray-200 bg-gray-50 px-3 py-2 text-left font-medium">
                {renderInlineText(text)}
              </th>
            ) : (
              <td key={cell.ulid} className="border border-gray-200 px-3 py-2 align-top">
                {renderInlineText(text)}
              </td>
            );
          })}
        </tr>
      );
    }

    case "cell":
      return <span>{renderInlineText(textForNode(node, childrenByParent))}</span>;

    default:
      return <p className="my-4 text-sm leading-7 text-gray-900">{nodePreview(node)}</p>;
  }
}

export function DocumentView() {
  const cartridge = useReader((s) => s.cartridge);
  const [nodes, setNodes] = useState<DocNode[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setNodes(null);
    setError(null);

    if (!cartridge) return;

    api
      .listAllNodes(cartridge.handle)
      .then((allNodes) => {
        if (!cancelled) setNodes(allNodes);
      })
      .catch((e) => {
        if (!cancelled) setError(errorToText(e));
      });

    return () => {
      cancelled = true;
    };
  }, [cartridge]);

  const grouped = useMemo(() => (nodes ? groupChildren(nodes) : null), [nodes]);

  if (error) {
    return <div className="p-6 text-sm text-red-700">{error}</div>;
  }

  if (!grouped || !cartridge) {
    return <div className="p-6 text-sm text-gray-500">Loading document...</div>;
  }

  const title = cartridge.meta.title ?? cartridge.meta.source_filename ?? "Document";
  const subtitle = [
    cartridge.meta.source_filename,
    cartridge.meta.source_format,
    cartridge.meta.word_count ? `${cartridge.meta.word_count.toLocaleString()} words` : null,
  ].filter(Boolean);

  return (
    <div className="h-full overflow-y-auto bg-white">
      <article className="max-w-3xl mx-auto px-8 py-8 text-gray-900">
        <header className="mb-8 pb-5 border-b border-gray-200">
          <h1 className="text-3xl font-semibold tracking-normal text-gray-950 mb-3">{title}</h1>
          {subtitle.length > 0 && (
            <div className="text-xs leading-5 text-gray-500">{subtitle.join(" | ")}</div>
          )}
        </header>
        {grouped.roots.map((root) => (
          <RenderNode
            key={root.ulid}
            node={root}
            childrenByParent={grouped.childrenByParent}
            depth={0}
          />
        ))}
      </article>
    </div>
  );
}
