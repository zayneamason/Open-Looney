import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { figureSrc } from "../figureSrc";
import { errorToText, useReader } from "../store";
import type { DocNode, DocNodeBrief, FigurePayload } from "../types";
import { metaNumber, metaString, nodePreview } from "../nodeDisplay";

type ChildrenByParent = Record<string, DocNode[]>;

export function nodeDomId(ulid: string): string {
  return `lun-node-${ulid}`;
}

/** Scroll Document view to a node (or nearest ancestor that has a DOM anchor). */
export function scrollDocumentToNode(
  ulid: string,
  parentChain?: DocNodeBrief[] | null,
): boolean {
  const tryIds = [ulid];
  if (parentChain?.length) {
    // parent_chain is root-first; try nearest parents first.
    for (let i = parentChain.length - 1; i >= 0; i--) {
      tryIds.push(parentChain[i].ulid);
    }
  }
  for (const id of tryIds) {
    const el = document.getElementById(nodeDomId(id));
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      return true;
    }
  }
  return false;
}

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

function nodeClickClass(selected: boolean): string {
  return selected
    ? "outline outline-2 outline-offset-2 outline-blue-400 rounded-sm"
    : "cursor-pointer hover:bg-blue-50/60 rounded-sm";
}

function FigureBlock({ node }: { node: DocNode }) {
  const cartridge = useReader((s) => s.cartridge);
  const selectFigure = useReader((s) => s.selectFigure);
  const selectNode = useReader((s) => s.selectNode);
  const selectedFigureUlid = useReader((s) => s.selectedFigureUlid);
  const selectedNode = useReader((s) => s.selectedNode);
  const [payload, setPayload] = useState<FigurePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const legacySrc = metaString(node, "src");
  const caption = node.content.trim();
  const selected =
    selectedFigureUlid === node.ulid || selectedNode?.ulid === node.ulid;

  useEffect(() => {
    let cancelled = false;
    if (!cartridge) return;

    setLoading(true);
    setError(null);
    api
      .getFigurePayload(cartridge.handle, node.ulid)
      .then((p) => {
        if (!cancelled) {
          setPayload(p);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(errorToText(e));
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [cartridge, node.ulid]);

  const imgSrc = figureSrc(payload) ?? legacySrc;

  function openFigure() {
    void selectNode(node.ulid);
    void selectFigure(node.ulid);
  }

  return (
    <figure
      id={nodeDomId(node.ulid)}
      className={`my-6 cursor-pointer rounded border p-2 transition-colors scroll-mt-8 ${
        selected
          ? "border-amber-400 bg-amber-50/40"
          : "border-transparent hover:border-gray-200 hover:bg-gray-50/50"
      }`}
      onClick={openFigure}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openFigure();
        }
      }}
      aria-label={caption ? `Open figure: ${caption}` : "Open figure inspector"}
    >
      {loading && (
        <div className="flex h-32 items-center justify-center rounded border border-dashed border-gray-200 bg-gray-50 text-xs text-gray-400">
          Loading figure…
        </div>
      )}
      {!loading && imgSrc && (
        <img
          src={imgSrc}
          alt={caption || "Figure"}
          className="max-w-full rounded border border-gray-200 bg-gray-50"
        />
      )}
      {!loading && !imgSrc && (
        <div className="flex h-24 items-center justify-center rounded border border-dashed border-gray-200 bg-gray-50 text-xs text-gray-400">
          {error ? error : "No image payload"}
        </div>
      )}
      {(caption || payload?.storage || legacySrc) && (
        <figcaption className="mt-2 flex flex-wrap items-baseline gap-x-2 text-xs leading-5 text-gray-500">
          <span>{caption || legacySrc || "Figure"}</span>
          {payload?.storage && (
            <span className="font-mono text-[10px] uppercase text-gray-400">
              {payload.storage}
              {payload.enrichments.length > 0
                ? ` · ${payload.enrichments.length} enrichments`
                : ""}
            </span>
          )}
        </figcaption>
      )}
    </figure>
  );
}

function useSelectInDocument() {
  const selectNode = useReader((s) => s.selectNode);
  const selectedNode = useReader((s) => s.selectedNode);
  return {
    selectedNode,
    selectInDocument(ulid: string) {
      void selectNode(ulid);
    },
  };
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
  const { selectedNode, selectInDocument } = useSelectInDocument();
  const selected = selectedNode?.ulid === node.ulid;

  switch (node.node_type) {
    case "document":
      return (
        <div id={nodeDomId(node.ulid)}>
          {children.map((child) => (
            <RenderNode
              key={child.ulid}
              node={child}
              childrenByParent={childrenByParent}
              depth={depth}
            />
          ))}
        </div>
      );

    case "section": {
      const title = node.content.trim() || metaString(node, "title");
      const pageNum = metaNumber(node, "page_num");
      const parsedLevel = metaNumber(node, "level");
      const headingLevel = Math.max(2, Math.min(6, parsedLevel ?? depth + 2));

      if (!title && pageNum !== null) {
        return (
          <section
            id={nodeDomId(node.ulid)}
            className="mt-10 pt-5 border-t border-gray-200 first:mt-0 first:border-t-0 scroll-mt-8"
          >
            <div
              className={`mb-4 text-[11px] font-mono uppercase tracking-wide text-gray-400 ${nodeClickClass(selected)}`}
              onClick={() => selectInDocument(node.ulid)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  selectInDocument(node.ulid);
                }
              }}
            >
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
        <section id={nodeDomId(node.ulid)} className="scroll-mt-8">
          <div
            className={nodeClickClass(selected)}
            onClick={() => selectInDocument(node.ulid)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                selectInDocument(node.ulid);
              }
            }}
          >
            <Heading level={headingLevel}>{title || `Section ${node.position + 1}`}</Heading>
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

    case "paragraph": {
      const sentenceKids = children.filter((c) => c.node_type === "sentence");
      const isBlockquote = node.meta_json?.blockquote === true;
      const Tag = isBlockquote ? "blockquote" : "p";
      const baseClass = isBlockquote
        ? "my-5 border-l-4 border-gray-300 pl-4 text-sm leading-7 text-gray-700"
        : "my-4 text-sm leading-7 text-gray-900";

      if (sentenceKids.length > 0) {
        return (
          <Tag
            id={nodeDomId(node.ulid)}
            className={`${baseClass} scroll-mt-8 ${nodeClickClass(selected)}`}
            onClick={() => selectInDocument(node.ulid)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                selectInDocument(node.ulid);
              }
            }}
          >
            {sentenceKids.map((s, i) => {
              const sSelected = selectedNode?.ulid === s.ulid;
              return (
                <span
                  key={s.ulid}
                  id={nodeDomId(s.ulid)}
                  className={`scroll-mt-8 ${sSelected ? "bg-blue-100 rounded-sm" : ""}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    selectInDocument(s.ulid);
                  }}
                >
                  {s.content.trim() || " "}
                  {i < sentenceKids.length - 1 ? " " : ""}
                </span>
              );
            })}
          </Tag>
        );
      }

      return (
        <Tag
          id={nodeDomId(node.ulid)}
          className={`${baseClass} scroll-mt-8 ${nodeClickClass(selected)}`}
          onClick={() => selectInDocument(node.ulid)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              selectInDocument(node.ulid);
            }
          }}
        >
          {renderInlineText(textForNode(node, childrenByParent))}
        </Tag>
      );
    }

    case "sentence":
      // Standalone sentence (not nested under a paragraph we already rendered).
      return (
        <p
          id={nodeDomId(node.ulid)}
          className={`my-4 text-sm leading-7 text-gray-900 scroll-mt-8 ${nodeClickClass(selected)}`}
          onClick={() => selectInDocument(node.ulid)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              selectInDocument(node.ulid);
            }
          }}
        >
          {renderInlineText(node.content.trim())}
        </p>
      );

    case "list":
      return (
        <ul
          id={nodeDomId(node.ulid)}
          className="my-4 list-disc pl-6 text-sm leading-7 text-gray-900 scroll-mt-8"
        >
          {children.map((child) => (
            <RenderNode
              key={child.ulid}
              node={child}
              childrenByParent={childrenByParent}
              depth={depth + 1}
            />
          ))}
        </ul>
      );

    case "list_item":
      return (
        <li
          id={nodeDomId(node.ulid)}
          className={`scroll-mt-8 ${nodeClickClass(selected)}`}
          onClick={() => selectInDocument(node.ulid)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              selectInDocument(node.ulid);
            }
          }}
        >
          {renderInlineText(textForNode(node, childrenByParent))}
        </li>
      );

    case "figure":
      return <FigureBlock node={node} />;

    case "image":
      return null;

    case "table": {
      const rows = children.filter((child) => child.node_type === "row");
      return (
        <div
          id={nodeDomId(node.ulid)}
          className={`my-6 overflow-x-auto scroll-mt-8 ${nodeClickClass(selected)}`}
          onClick={() => selectInDocument(node.ulid)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              selectInDocument(node.ulid);
            }
          }}
        >
          <table className="min-w-full border-collapse text-sm">
            <tbody>
              {rows.map((row) => (
                <RenderNode
                  key={row.ulid}
                  node={row}
                  childrenByParent={childrenByParent}
                  depth={depth + 1}
                />
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
        <tr id={nodeDomId(node.ulid)}>
          {cells.map((cell) => {
            const text = textForNode(cell, childrenByParent);
            return isHeader ? (
              <th
                key={cell.ulid}
                id={nodeDomId(cell.ulid)}
                className="border border-gray-200 bg-gray-50 px-3 py-2 text-left font-medium"
              >
                {renderInlineText(text)}
              </th>
            ) : (
              <td
                key={cell.ulid}
                id={nodeDomId(cell.ulid)}
                className="border border-gray-200 px-3 py-2 align-top"
              >
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
      return (
        <p id={nodeDomId(node.ulid)} className="my-4 text-sm leading-7 text-gray-900 scroll-mt-8">
          {nodePreview(node)}
        </p>
      );
  }
}

export function DocumentView() {
  const cartridge = useReader((s) => s.cartridge);
  const selectedNode = useReader((s) => s.selectedNode);
  const view = useReader((s) => s.view);
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

  // Hierarchy / search selection → scroll Document to that node.
  useEffect(() => {
    if (view !== "document" || !selectedNode || !nodes) return;
    const handle = window.requestAnimationFrame(() => {
      scrollDocumentToNode(selectedNode.ulid, selectedNode.parent_chain);
    });
    return () => window.cancelAnimationFrame(handle);
  }, [view, selectedNode?.ulid, nodes]);

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
