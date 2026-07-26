import type { Meta } from "./types";

export const SEMANTIC_EMBEDDING_MODEL = "all-MiniLM-L6-v2";
export const SEMANTIC_EMBEDDING_DIM = 384;

/** Whether a cartridge's stored embeddings match the bundled query model. */
export function isSemanticSearchAvailable(meta: Meta): boolean {
  return (
    meta.embedding_model === SEMANTIC_EMBEDDING_MODEL &&
    meta.embedding_dim === SEMANTIC_EMBEDDING_DIM
  );
}
