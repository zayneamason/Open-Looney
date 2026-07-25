# Intimate Searchable Figures — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove intimate searchable figures end-to-end for Markdown+PNG: `figure` → `image` + hybrid payload + FTS-visible caption/alt, with schema hooks for later enrichment — without COG tiles or chat `images.db` changes.

**Architecture:** Additive LUNC law (SPEC-013 research draft / Approach 2). Parser emits `figure` wrapping `image`; builder writes `media_blobs` (embedded by default for small PNGs) and keeps linguistic spine on `figure.content` for FTS. Enrichment (taxonomy, visual_description, discourse, OCR, regions) is later tasks behind flags. Engine repo implements; Open-Looney holds fixtures, AUDIT, and format notes.

**Tech Stack:** Python 3.11+, SQLite FTS5, Luna Engine `luna.cartridge` (v0.3 schema), pytest, Pillow (dims/hash only for spike; OCR optional via pytesseract later).

## Global Constraints

- Research-primary SPEC-013 is **not accepted**; spike may land behind builder behavior that is additive and safe for v0.3 readers (ignore unknown types/tables).
- Do **not** modify chat `substrate/images.py` / `images.db`.
- Do **not** implement COG / GeoPackage tile matrices / CRS (media-family RFC).
- Do **not** require GDAL for PNG/JPEG; GDAL remains optional later for TIFF/GeoTIFF.
- `region` nodes are **out of scope** for Tasks 1–5 (schema comment / reserved only).
- Prefer embedded storage for spike fixtures under 256 KiB; support `storage=external` path in schema even if tests cover embedded first.
- Commits: Engine changes in `LunaEngineBetaV2.0`; Open-Looney docs/fixtures in `Open-Looney`. Do not mix repos in one commit.
- TDD: failing test → implement → pass → commit each task.

## File map

| Path | Responsibility |
|---|---|
| `Open-Looney/09_Sample_Sources/searchable_figures/` | Tiny Markdown + PNG fixture for spike/AUDIT |
| `Open-Looney/04_Audits/AUDIT_YYYY-MM-DD_searchable-figures-spike.md` | Post-spike sqlite3 audit notes |
| `Open-Looney/03_Format_Spec/` (appendix or draft note) | Document `media_blobs` + figure/image roles when spike lands |
| `LunaEngine…/src/luna/cartridge/schema.py` | Add `media_blobs` DDL |
| `LunaEngine…/src/luna/cartridge/parsers/markdown.py` | Emit `figure` + child `image` with resolved path meta |
| `LunaEngine…/src/luna/cartridge/builder.py` | Persist `media_blobs`; optional `--figures` always-on for MD images |
| `LunaEngine…/src/luna/cartridge/media.py` (new) | Hash, dims, embed-vs-external policy helpers |
| `LunaEngine…/src/luna/cartridge/validation.py` | `validate_media_blobs` |
| `LunaEngine…/tests/test_cartridge_searchable_figures.py` (new) | Parser + builder + FTS spike tests |

**Repos:** Engine root `_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root` (remote `LunaEngineBetaV2.0`). Open-Looney root `_HeyLuna_BETA/Research/Open-Looney`.

---

### Task 1: Open-Looney fixture (Markdown + PNG)

**Files:**
- Create: `09_Sample_Sources/searchable_figures/ambassador_protocol.md`
- Create: `09_Sample_Sources/searchable_figures/diagram_ambassador.png` (tiny valid PNG, ≤20 KiB)
- Create: `09_Sample_Sources/searchable_figures/README.md` (one paragraph: purpose + non-goals)

**Interfaces:**
- Consumes: nothing
- Produces: fixture paths used by Engine tests (copy or path override) and AUDIT

- [ ] **Step 1: Create a minimal valid PNG**

Use Python once (no Engine import required):

```bash
cd "/Users/zayneamason/_HeyLuna_BETA/Research/Open-Looney"
python3 - <<'PY'
from pathlib import Path
try:
    from PIL import Image, ImageDraw
except ImportError:
    raise SystemExit("pip install pillow")
out = Path("09_Sample_Sources/searchable_figures")
out.mkdir(parents=True, exist_ok=True)
img = Image.new("RGB", (120, 80), (240, 240, 255))
d = ImageDraw.Draw(img)
d.rectangle((10, 10, 110, 70), outline=(20, 40, 120), width=2)
d.text((20, 30), "Ambassador", fill=(20, 40, 120))
img.save(out / "diagram_ambassador.png")
print("wrote", out / "diagram_ambassador.png")
PY
```

- [ ] **Step 2: Write the Markdown source**

```markdown
# Searchable Figures Spike

## Protocol overview

The following diagram summarizes the Ambassador protocol handshake.

![Ambassador protocol diagram](diagram_ambassador.png)

Surrounding prose mentions **Ambassador protocol** so discourse tests can find neighbors later.
```

Save as `09_Sample_Sources/searchable_figures/ambassador_protocol.md`.

- [ ] **Step 3: README**

```markdown
# searchable_figures fixture

Markdown + PNG for SPEC-013 intimate-figure spike.
Not a ceremonial cartridge. No COG/tiles. Engine tests may copy these files into tmp_path.
```

- [ ] **Step 4: Commit (Open-Looney)**

```bash
cd "/Users/zayneamason/_HeyLuna_BETA/Research/Open-Looney"
git add 09_Sample_Sources/searchable_figures/
git commit -m "$(cat <<'EOF'
docs: add Markdown+PNG fixture for searchable figures spike

EOF
)"
```

---

### Task 2: Engine — `media_blobs` schema + unit helpers

**Files:**
- Modify: `src/luna/cartridge/schema.py` (append `media_blobs` + indexes after `doc_nodes` indexes block)
- Create: `src/luna/cartridge/media.py`
- Create: `tests/test_cartridge_media_helpers.py`

**Interfaces:**
- Consumes: `doc_nodes.ulid` FK target
- Produces:
  - `MEDIA_BLOBS_DDL` included in `LUN_SCHEMA`
  - `def sha256_file(path: Path) -> str`
  - `def image_dims(path: Path) -> tuple[int, int] | None`
  - `def choose_storage(byte_len: int, *, embed_max: int = 262144) -> Literal["embedded", "external"]`
  - `def build_media_blob_row(node_ulid: str, path: Path, *, embed_max: int = 262144) -> dict`

- [ ] **Step 1: Write failing helper tests**

```python
# tests/test_cartridge_media_helpers.py
from pathlib import Path
import hashlib
import pytest
from luna.cartridge.media import choose_storage, sha256_file, build_media_blob_row

def test_choose_storage_embeds_small():
    assert choose_storage(1000) == "embedded"

def test_choose_storage_external_when_large():
    assert choose_storage(300_000) == "external"

def test_sha256_file_matches_hashlib(tmp_path: Path):
    p = tmp_path / "a.bin"
    data = b"lun-figure"
    p.write_bytes(data)
    assert sha256_file(p) == hashlib.sha256(data).hexdigest()

def test_build_media_blob_row_embedded(tmp_path: Path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    row = build_media_blob_row("01ARZ3NDEKTSV4RRFFQ69G5FAV", p, embed_max=262144)
    assert row["storage"] == "embedded"
    assert row["bytes"] == p.read_bytes()
    assert row["external_path"] is None
    assert len(row["sha256"]) == 64
    assert row["node_ulid"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
```

- [ ] **Step 2: Run tests — expect fail**

```bash
cd "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root"
python -m pytest tests/test_cartridge_media_helpers.py -v
```

Expected: `ModuleNotFoundError` or import errors for `luna.cartridge.media`.

- [ ] **Step 3: Implement `media.py`**

```python
# src/luna/cartridge/media.py
from __future__ import annotations
import hashlib
import mimetypes
from pathlib import Path
from typing import Literal

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def image_dims(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(path) as im:
        return int(im.width), int(im.height)

def choose_storage(byte_len: int, *, embed_max: int = 262144) -> Literal["embedded", "external"]:
    return "embedded" if byte_len <= embed_max else "external"

def build_media_blob_row(
    node_ulid: str,
    path: Path,
    *,
    embed_max: int = 262144,
) -> dict:
    path = Path(path)
    data = path.read_bytes()
    storage = choose_storage(len(data), embed_max=embed_max)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    dims = image_dims(path)
    width, height = (dims if dims else (None, None))
    return {
        "node_ulid": node_ulid,
        "media_type": media_type,
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(data).hexdigest(),
        "storage": storage,
        "bytes": data if storage == "embedded" else None,
        "external_path": None if storage == "embedded" else str(path.resolve()),
    }
```

- [ ] **Step 4: Append DDL to `LUN_SCHEMA` in `schema.py`**

```sql
-- SPEC-013 research spike: raster payload beside image nodes (additive).
CREATE TABLE IF NOT EXISTS media_blobs (
    node_ulid TEXT PRIMARY KEY,
    media_type TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    sha256 TEXT NOT NULL,
    storage TEXT NOT NULL CHECK (storage IN ('embedded', 'external')),
    bytes BLOB,
    external_path TEXT,
    FOREIGN KEY (node_ulid) REFERENCES doc_nodes(ulid),
    CHECK (
        (storage = 'embedded' AND bytes IS NOT NULL AND external_path IS NULL)
        OR (storage = 'external' AND bytes IS NULL AND external_path IS NOT NULL)
    )
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_media_blobs_sha256 ON media_blobs(sha256);
```

Place after `doc_nodes` indexes / before `extractions` is fine; keep `CREATE TABLE IF NOT EXISTS` so older tooling tolerates re-runs.

- [ ] **Step 5: Re-run helper tests — expect pass**

```bash
python -m pytest tests/test_cartridge_media_helpers.py -v
```

Expected: PASS

- [ ] **Step 6: Commit (Engine)**

```bash
git add src/luna/cartridge/media.py src/luna/cartridge/schema.py tests/test_cartridge_media_helpers.py
git commit -m "$(cat <<'EOF'
feat(cartridge): add media_blobs schema and payload helpers

EOF
)"
```

---

### Task 3: Markdown parser — `figure` wraps `image`

**Files:**
- Modify: `src/luna/cartridge/parsers/markdown.py` (image branch ~lines 117–127)
- Create: `tests/test_cartridge_markdown_figures.py`

**Interfaces:**
- Consumes: `_IMAGE` regex; `_make_node`
- Produces: node list where an image line yields:
  1. `figure` with `content=alt or None`, `meta` may include `figure_label` later
  2. child `image` with `content=None`, `meta={"src": <resolved or relative>, "src_resolved": <absolute str or null>}`

Resolution rule: if `src` is relative, resolve against `source_path.parent`; if missing file, still emit nodes with `src_resolved: null` and `missing: true` in image meta (builder skips `media_blobs` for missing).

- [ ] **Step 1: Write failing parser tests**

```python
# tests/test_cartridge_markdown_figures.py
from pathlib import Path
from luna.cartridge.parsers.markdown import MarkdownParser

def test_markdown_image_emits_figure_wrapping_image(tmp_path: Path):
    png = tmp_path / "d.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    md = tmp_path / "doc.md"
    md.write_text("# T\n\n![Alt text here](d.png)\n")
    nodes = MarkdownParser().parse(md)
    types = [n["type"] for n in nodes]
    assert "figure" in types
    assert "image" in types
    fig_i = types.index("figure")
    img_i = types.index("image")
    assert img_i > fig_i
    assert nodes[img_i]["parent_idx"] == fig_i
    assert nodes[fig_i]["content"] == "Alt text here"
    assert nodes[img_i]["meta"]["src"] == "d.png"
    assert nodes[img_i]["meta"]["src_resolved"] == str(png.resolve())

def test_markdown_missing_image_still_emits_nodes(tmp_path: Path):
    md = tmp_path / "doc.md"
    md.write_text("![gone](nope.png)\n")
    nodes = MarkdownParser().parse(md)
    img = next(n for n in nodes if n["type"] == "image")
    assert img["meta"]["missing"] is True
    assert img["meta"]["src_resolved"] is None
```

- [ ] **Step 2: Run — expect fail**

```bash
python -m pytest tests/test_cartridge_markdown_figures.py -v
```

Expected: FAIL (no `image` type / wrong parent).

- [ ] **Step 3: Replace image branch in `markdown.py`**

Replace the single `figure` append with:

```python
            img_match = _IMAGE.match(line.strip())
            if img_match:
                alt = img_match.group(1)
                src = img_match.group(2)
                parent = section_stack[-1][0] if section_stack else root_idx
                pos = sibling_count[parent]
                sibling_count[parent] += 1

                resolved = None
                missing = True
                # source_path is available in parse(); thread it into _parse_text
                # as base_dir: Path | None
                if base_dir is not None:
                    candidate = (base_dir / src).resolve() if not Path(src).is_absolute() else Path(src)
                    if candidate.is_file():
                        resolved = str(candidate)
                        missing = False

                fig_idx = len(nodes)
                nodes.append(_make_node("figure", alt or None, parent, pos, None))
                sibling_count[fig_idx] = 0
                nodes.append(_make_node(
                    "image",
                    None,
                    fig_idx,
                    0,
                    {"src": src, "src_resolved": resolved, "missing": missing},
                ))
                i += 1
                continue
```

Update `parse` / `_parse_text` signatures so `base_dir = source_path.parent` is passed.

Also update the list-item / paragraph lookahead that checks `_IMAGE` if any (keep behavior: images not swallowed into paragraphs).

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_cartridge_markdown_figures.py -v
```

- [ ] **Step 5: Commit (Engine)**

```bash
git add src/luna/cartridge/parsers/markdown.py tests/test_cartridge_markdown_figures.py
git commit -m "$(cat <<'EOF'
feat(cartridge): markdown figures wrap image child nodes

EOF
)"
```

---

### Task 4: Builder persists `media_blobs` + FTS spine

**Files:**
- Modify: `src/luna/cartridge/builder.py` (after `doc_nodes` insert loop)
- Create: `tests/test_cartridge_searchable_figures.py`

**Interfaces:**
- Consumes: `build_media_blob_row`, parser `image.meta.src_resolved`
- Produces: for each non-missing `image` node, one `media_blobs` row; `figure.content` remains alt (FTS via existing triggers)

- [ ] **Step 1: Write failing builder/FTS test**

```python
# tests/test_cartridge_searchable_figures.py
from __future__ import annotations
import asyncio
import sqlite3
from pathlib import Path
import pytest
from luna.cartridge.builder import CartridgeBuilder

@pytest.fixture
def figure_source(tmp_path: Path) -> Path:
    png = tmp_path / "diagram_ambassador.png"
    # minimal PNG header-ish bytes are enough for hash; dims may be None without Pillow
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    md = tmp_path / "ambassador_protocol.md"
    md.write_text(
        "# Spike\n\n"
        "![Ambassador protocol diagram](diagram_ambassador.png)\n\n"
        "Prose about Ambassador protocol.\n"
    )
    return md

def test_builder_writes_media_blob_and_fts_hits_caption(figure_source: Path):
    builder = CartridgeBuilder(extract=False, embed=False)
    out = asyncio.run(builder.build(figure_source))
    conn = sqlite3.connect(str(out))
    conn.row_factory = sqlite3.Row

    fig = conn.execute(
        "SELECT ulid, content FROM doc_nodes WHERE type = 'figure'"
    ).fetchone()
    assert fig is not None
    assert "Ambassador" in (fig["content"] or "")

    img = conn.execute(
        "SELECT ulid, parent_ulid FROM doc_nodes WHERE type = 'image'"
    ).fetchone()
    assert img is not None
    assert img["parent_ulid"] == fig["ulid"]

    blob = conn.execute(
        "SELECT sha256, storage, bytes IS NOT NULL AS has_bytes FROM media_blobs WHERE node_ulid = ?",
        (img["ulid"],),
    ).fetchone()
    assert blob is not None
    assert blob["storage"] == "embedded"
    assert blob["has_bytes"] == 1
    assert len(blob["sha256"]) == 64

    hits = conn.execute(
        "SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH 'Ambassador'"
    ).fetchall()
    assert len(hits) >= 1
    conn.close()
```

- [ ] **Step 2: Run — expect fail**

```bash
python -m pytest tests/test_cartridge_searchable_figures.py -v
```

Expected: FAIL on missing `media_blobs` table or empty rows.

- [ ] **Step 3: Builder write loop**

After the `doc_nodes` insert loop (where `idx_to_ulid` is complete), add:

```python
        from .media import build_media_blob_row

        for idx, node in enumerate(nodes):
            if node.get("type") != "image":
                continue
            meta = node.get("meta") or {}
            if meta.get("missing") or not meta.get("src_resolved"):
                continue
            row = build_media_blob_row(idx_to_ulid[idx], Path(meta["src_resolved"]))
            conn.execute(
                """
                INSERT INTO media_blobs (
                    node_ulid, media_type, width, height, sha256,
                    storage, bytes, external_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["node_ulid"], row["media_type"], row["width"], row["height"],
                    row["sha256"], row["storage"], row["bytes"], row["external_path"],
                ),
            )
```

Ensure `LUN_SCHEMA` already created `media_blobs` (Task 2). No `user_version` bump for this additive spike; document in Open-Looney AUDIT that format bump awaits SPEC acceptance.

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_cartridge_searchable_figures.py tests/test_cartridge_markdown_figures.py tests/test_cartridge_media_helpers.py tests/test_cartridge_builder_v03.py -v
```

Expected: all PASS (v0.3 builder tests still green).

- [ ] **Step 5: Commit (Engine)**

```bash
git add src/luna/cartridge/builder.py tests/test_cartridge_searchable_figures.py
git commit -m "$(cat <<'EOF'
feat(cartridge): persist media_blobs for markdown image nodes

EOF
)"
```

---

### Task 5: Validation — `validate_media_blobs`

**Files:**
- Modify: `src/luna/cartridge/validation.py`
- Modify: `src/luna/cartridge/builder.py` (call validator before finalize when table exists)
- Modify: `tests/test_cartridge_searchable_figures.py` (add negative case)

**Interfaces:**
- Produces: `def validate_media_blobs(conn: sqlite3.Connection) -> None` raises `BuildError`

Rules:
1. Every `media_blobs` row references an existing `doc_nodes.ulid` with `type='image'`.
2. `storage` discriminant matches NULL-ness of `bytes` / `external_path`.
3. `sha256` is 64 hex chars.

- [ ] **Step 1: Failing test for bad discriminant**

```python
def test_validate_media_blobs_rejects_inconsistent_storage(figure_source: Path):
    from luna.cartridge.validation import validate_media_blobs, BuildError
    builder = CartridgeBuilder(extract=False, embed=False)
    out = asyncio.run(builder.build(figure_source))
    conn = sqlite3.connect(str(out))
    conn.execute(
        "UPDATE media_blobs SET external_path = 'x', bytes = NULL WHERE storage = 'embedded'"
    )
    with pytest.raises(BuildError):
        validate_media_blobs(conn)
    conn.close()
```

- [ ] **Step 2: Implement validator**

```python
def validate_media_blobs(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_blobs'"
    ).fetchone()
    if not exists:
        return
    rows = conn.execute(
        "SELECT node_ulid, storage, bytes, external_path, sha256 FROM media_blobs"
    ).fetchall()
    for node_ulid, storage, blob, ext, sha in rows:
        ntype = conn.execute(
            "SELECT type FROM doc_nodes WHERE ulid = ?", (node_ulid,)
        ).fetchone()
        if ntype is None or ntype[0] != "image":
            raise BuildError(f"media_blobs {node_ulid}: missing image node")
        if storage == "embedded" and (blob is None or ext is not None):
            raise BuildError(f"media_blobs {node_ulid}: embedded discriminant violated")
        if storage == "external" and (blob is not None or not ext):
            raise BuildError(f"media_blobs {node_ulid}: external discriminant violated")
        if not isinstance(sha, str) or len(sha) != 64:
            raise BuildError(f"media_blobs {node_ulid}: bad sha256")
```

Call from builder after media inserts / before commit (or after commit with raise before return). Match existing validator call style in `builder.py`.

- [ ] **Step 3: Run tests — pass**

```bash
python -m pytest tests/test_cartridge_searchable_figures.py -v
```

- [ ] **Step 4: Commit (Engine)**

```bash
git add src/luna/cartridge/validation.py src/luna/cartridge/builder.py tests/test_cartridge_searchable_figures.py
git commit -m "$(cat <<'EOF'
feat(cartridge): validate media_blobs invariants

EOF
)"
```

---

### Task 6: Open-Looney AUDIT + journal note (research close of spike)

**Files:**
- Create: `04_Audits/AUDIT_YYYY-MM-DD_searchable-figures-spike.md` (use spike day)
- Modify: `08_Journal/<today>.md` (short entry)
- Modify: `01_Specs/active/SPEC-013_searchable-figures.md` decision log (spike evidence pointer only; do **not** mark accepted)

**Interfaces:** none (docs)

- [ ] **Step 1: Build fixture cartridge with Engine**

```bash
cd "/Users/zayneamason/_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root"
python -m luna.cartridge.builder \
  "/Users/zayneamason/_HeyLuna_BETA/Research/Open-Looney/09_Sample_Sources/searchable_figures/ambassador_protocol.md" \
  "/tmp/ambassador_protocol.lun"
```

- [ ] **Step 2: sqlite3 AUDIT commands (paste results into AUDIT file)**

```bash
sqlite3 /tmp/ambassador_protocol.lun <<'SQL'
.headers on
SELECT type, content, ulid FROM doc_nodes WHERE type IN ('figure','image');
SELECT node_ulid, storage, length(sha256), length(bytes) FROM media_blobs;
SELECT snippet(nodes_fts, 0, '>>>', '<<<', '…', 10)
  FROM nodes_fts WHERE nodes_fts MATCH 'Ambassador';
SQL
```

- [ ] **Step 3: Write AUDIT markdown** summarizing counts, FTS hit, non-goals still deferred (OCR, taxonomy enrichment, regions, PDF images, COG).

- [ ] **Step 4: Commit (Open-Looney)**

```bash
git add 04_Audits/AUDIT_*_searchable-figures-spike.md 08_Journal/ 01_Specs/active/SPEC-013_searchable-figures.md
git commit -m "$(cat <<'EOF'
docs: AUDIT searchable figures Markdown+PNG spike

EOF
)"
```

---

### Task 7 (optional follow-on): Tiered enrichment stubs — defer unless requested

**Out of default execution.** Only when Ahab asks to continue enrichment:

1. Rule-based `media_classification` extraction (`media_kind` from filename/alt heuristics) + `extraction_sources` → figure ULID.
2. Optional OCR child content via pytesseract when available (`extraction_method='rule'`).
3. Discourse: link figure to previous/next paragraph ULIDs via `extraction_context_nodes`.
4. PDF image XObjects (stop skipping `type != 0` blocks) — separate plan slice.
5. `region` nodes — separate plan slice after layout/OCR design.

Do not start Task 7 in the same PR as Tasks 1–6.

---

## Self-review (plan vs SPEC-013)

| SPEC / design requirement | Task coverage |
|---|---|
| `figure` wraps `image` | Task 3–4 |
| Hybrid `media_blobs` + sha256 | Task 2, 4–5 |
| FTS linguistic spine (caption/alt) | Task 4 |
| Tiered enrichment (taxonomy/visual/discourse) | Task 7 optional — not required for spike success |
| Regions reserved | Explicit non-goal Tasks 1–6 |
| GDAL optional | Not in spike |
| Chat images.db unchanged | Global constraint |
| COG deferred | Global constraint |
| sqlite3 auditability | Task 6 |
| v0.3 readers safe | Additive table/types; no acceptance bump |

Placeholder scan: no TBD steps in Tasks 1–6. Exact extraction type strings left to Task 7 by design.

---

## Execution handoff

Plan complete and saved to:

`Research/Open-Looney/06_Prototypes/PLAN_2026-07-24_intimate-searchable-figures.md`

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — execute tasks in this session with checkpoints  

**Which approach?**  

Reminder: Tasks 1–6 are the spike; Task 7 stays parked unless you ask. Still no SPEC-013 acceptance and no COG/media-family work in this plan.
