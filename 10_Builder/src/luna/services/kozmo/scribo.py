"""
SCRIBO Service Layer

Manages SCRIBO documents within a KOZMO project.
.scribo files are source of truth. Story tree is derived.

This module KNOWS about projects and directories.
It calls scribo_parser.py for document-level operations.

Responsibilities:
- Read/write .scribo documents
- Build story tree from directory structure
- Container CRUD (acts, chapters, etc.)
- Word count computation
- Character extraction + CODEX cross-reference
- Luna note management
- Story search

Directory Structure:
story/
├── _structure.yaml          # Hierarchy definition + ordering
├── act_1/
│   ├── _meta.yaml           # Act metadata
│   ├── ch_01/
│   │   ├── _meta.yaml       # Chapter metadata
│   │   ├── sc_01_crooked_nail.scribo
│   │   └── sc_02_what_he_left.scribo
│   └── ch_02/
│       ├── _meta.yaml
│       └── sc_03_first_fix.scribo
└── act_2/
    └── ...
"""

import yaml
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from .types import (
    StoryLevel,
    StoryStructure,
    ContainerMeta,
    ScriboFrontmatter,
    ScriboDocument,
    LunaNote,
    StoryTreeNode,
)
from .scribo_parser import (
    parse_scribo,
    serialize_scribo,
    extract_fountain_elements,
    word_count,
)
from .entity import slugify


# =============================================================================
# ScriboService
# =============================================================================


class ScriboService:
    """
    Manages SCRIBO documents within a KOZMO project.
    .scribo files are source of truth. Story tree is derived.
    """

    def __init__(self, project_root: Path):
        self.root = project_root
        self.story_dir = project_root / "story"

    # =========================================================================
    # Structure
    # =========================================================================

    def get_structure(self) -> StoryStructure:
        """Read _structure.yaml — hierarchy definition + ordering."""
        structure_file = self.story_dir / "_structure.yaml"

        if not structure_file.exists():
            return StoryStructure()

        with open(structure_file, "r") as f:
            data = yaml.safe_load(f)

        if not data:
            return StoryStructure()

        levels = [
            StoryLevel(name=lv["name"], slug_prefix=lv["slug_prefix"])
            for lv in data.get("levels", [])
        ]

        return StoryStructure(
            levels=levels,
            order=data.get("order", {}),
        )

    def save_structure(self, structure: StoryStructure) -> None:
        """Write _structure.yaml. Called when scenes are reordered."""
        structure_file = self.story_dir / "_structure.yaml"
        self.story_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "levels": [
                {"name": lv.name, "slug_prefix": lv.slug_prefix}
                for lv in structure.levels
            ],
            "order": structure.order,
        }

        with open(structure_file, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # =========================================================================
    # Story Tree
    # =========================================================================

    def build_story_tree(self) -> StoryTreeNode:
        """
        Walk the story directory and build a recursive tree.
        Uses _structure.yaml for ordering, falls back to directory scan.
        Computes word counts at every level.
        """
        structure = self.get_structure()

        if structure.order:
            return self._build_tree_from_structure(structure)

        # Fallback: scan directories
        return self._build_tree_from_scan()

    def _build_tree_from_structure(self, structure: StoryStructure) -> StoryTreeNode:
        """Build tree using _structure.yaml ordering."""
        root_node = StoryTreeNode(
            id="root",
            title="Story",
            type="root",
            status="draft",
        )

        def _build_children(order_data: Any, parent_dir: Path) -> List[StoryTreeNode]:
            children = []

            if isinstance(order_data, list):
                # List of items — could be scene slugs or container dicts
                for item in order_data:
                    if isinstance(item, str):
                        # Scene slug
                        node = self._load_scene_node(item, parent_dir)
                        if node:
                            children.append(node)
                    elif isinstance(item, dict):
                        for container_slug, sub_order in item.items():
                            node = self._load_container_node(
                                container_slug, parent_dir, sub_order
                            )
                            children.append(node)

            elif isinstance(order_data, dict):
                for container_slug, sub_order in order_data.items():
                    node = self._load_container_node(
                        container_slug, parent_dir, sub_order
                    )
                    children.append(node)

            return children

        root_node.children = _build_children(structure.order, self.story_dir)

        # Compute aggregate word counts
        self._compute_word_counts(root_node)

        return root_node

    def _load_container_node(
        self, slug: str, parent_dir: Path, sub_order: Any
    ) -> StoryTreeNode:
        """Load a container node from _meta.yaml."""
        container_dir = parent_dir / slug

        meta = self._load_meta(container_dir)
        node = StoryTreeNode(
            id=slug,
            title=meta.title if meta else slug,
            type=meta.level if meta else "container",
            status=meta.status if meta else "idea",
        )

        # Recurse into children
        if isinstance(sub_order, list):
            for item in sub_order:
                if isinstance(item, str):
                    scene_node = self._load_scene_node(item, container_dir)
                    if scene_node:
                        node.children.append(scene_node)
                elif isinstance(item, dict):
                    for child_slug, child_order in item.items():
                        child_node = self._load_container_node(
                            child_slug, container_dir, child_order
                        )
                        node.children.append(child_node)
        elif isinstance(sub_order, dict):
            for child_slug, child_order in sub_order.items():
                child_node = self._load_container_node(
                    child_slug, container_dir, child_order
                )
                node.children.append(child_node)

        return node

    def _load_scene_node(self, slug: str, parent_dir: Path) -> Optional[StoryTreeNode]:
        """Load a scene node from a .scribo file."""
        # Search for the .scribo file in parent_dir
        scribo_files = list(parent_dir.glob(f"{slug}*.scribo")) + list(
            parent_dir.glob(f"**/{slug}*.scribo")
        )

        if not scribo_files:
            # Scene referenced in structure but file not found
            return StoryTreeNode(
                id=slug,
                title=slug,
                type="scene",
                status="idea",
            )

        scribo_file = scribo_files[0]
        try:
            text = scribo_file.read_text(encoding="utf-8")
            fm, body = parse_scribo(text)
            wc = word_count(body)
            return StoryTreeNode(
                id=slug,
                title=slug.replace("_", " ").title(),
                type="scene",
                status=fm.status,
                word_count=wc,
            )
        except Exception:
            return StoryTreeNode(id=slug, title=slug, type="scene", status="error")

    def _build_tree_from_scan(self) -> StoryTreeNode:
        """Fallback: scan story directory for containers and scenes."""
        root = StoryTreeNode(
            id="root", title="Story", type="root", status="draft"
        )

        if not self.story_dir.exists():
            return root

        for path in sorted(self.story_dir.iterdir()):
            if path.name.startswith("_"):
                continue
            if path.is_dir():
                node = self._scan_container(path)
                root.children.append(node)
            elif path.suffix == ".scribo":
                node = self._scan_scene(path)
                if node:
                    root.children.append(node)

        self._compute_word_counts(root)
        return root

    def _scan_container(self, dir_path: Path) -> StoryTreeNode:
        """Recursively scan a container directory."""
        meta = self._load_meta(dir_path)
        node = StoryTreeNode(
            id=dir_path.name,
            title=meta.title if meta else dir_path.name.replace("_", " ").title(),
            type=meta.level if meta else "container",
            status=meta.status if meta else "idea",
        )

        for path in sorted(dir_path.iterdir()):
            if path.name.startswith("_"):
                continue
            if path.is_dir():
                child = self._scan_container(path)
                node.children.append(child)
            elif path.suffix == ".scribo":
                scene = self._scan_scene(path)
                if scene:
                    node.children.append(scene)

        return node

    def _scan_scene(self, file_path: Path) -> Optional[StoryTreeNode]:
        """Load scene info from a .scribo file for tree display."""
        try:
            text = file_path.read_text(encoding="utf-8")
            fm, body = parse_scribo(text)
            wc = word_count(body)
            return StoryTreeNode(
                id=file_path.stem,
                title=file_path.stem.replace("_", " ").title(),
                type="scene",
                status=fm.status,
                word_count=wc,
            )
        except Exception:
            return None

    def _load_meta(self, dir_path: Path) -> Optional[ContainerMeta]:
        """Load _meta.yaml from a container directory."""
        meta_file = dir_path / "_meta.yaml"
        if not meta_file.exists():
            return None
        try:
            with open(meta_file, "r") as f:
                data = yaml.safe_load(f)
            if not data:
                return None
            return ContainerMeta(
                title=data.get("title", dir_path.name),
                slug=data.get("slug", dir_path.name),
                level=data.get("level", "container"),
                status=data.get("status", "idea"),
                summary=data.get("summary"),
                notes=data.get("notes"),
            )
        except Exception:
            return None

    def _compute_word_counts(self, node: StoryTreeNode) -> int:
        """Recursively compute word counts (sum of children)."""
        if not node.children:
            return node.word_count

        total = 0
        for child in node.children:
            total += self._compute_word_counts(child)

        node.word_count = total
        return total

    # =========================================================================
    # Document CRUD
    # =========================================================================

    def get_document(self, scene_slug: str) -> Optional[ScriboDocument]:
        """Read and parse a .scribo file by slug."""
        scribo_file = self._find_scribo_file(scene_slug)
        if not scribo_file:
            return None

        text = scribo_file.read_text(encoding="utf-8")
        fm, body = parse_scribo(text)
        wc = word_count(body)

        # Load luna notes if they exist in a sidecar
        luna_notes = self._load_luna_notes(scene_slug)

        rel_path = str(scribo_file.relative_to(self.root))

        return ScriboDocument(
            slug=scene_slug,
            path=rel_path,
            frontmatter=fm,
            body=body,
            word_count=wc,
            luna_notes=luna_notes,
        )

    def save_document(self, doc: ScriboDocument) -> ScriboDocument:
        """
        Write .scribo file back to disk.
        Recomputes word count.
        """
        scribo_file = self._find_scribo_file(doc.slug)
        if not scribo_file:
            # Use the path from the document
            scribo_file = self.root / doc.path

        content = serialize_scribo(doc.frontmatter, doc.body)
        scribo_file.parent.mkdir(parents=True, exist_ok=True)
        scribo_file.write_text(content, encoding="utf-8")

        # Update word count
        doc.word_count = word_count(doc.body)

        # Save luna notes if present
        if doc.luna_notes:
            self._save_luna_notes(doc.slug, doc.luna_notes)

        return doc

    def create_document(
        self,
        container_slug: str,
        title: str,
        doc_type: str = "scene",
    ) -> ScriboDocument:
        """
        Create a new .scribo file with default frontmatter.
        Adds to _structure.yaml ordering.
        """
        slug = slugify(title)

        # Find the container directory
        container_dir = self._find_container_dir(container_slug)
        if not container_dir:
            container_dir = self.story_dir / container_slug
            container_dir.mkdir(parents=True, exist_ok=True)

        scribo_file = container_dir / f"{slug}.scribo"

        fm = ScriboFrontmatter(
            type=doc_type,
            container=container_slug,
            status="idea",
        )

        body = f"# {title}\n\n"
        content = serialize_scribo(fm, body)
        scribo_file.write_text(content, encoding="utf-8")

        rel_path = str(scribo_file.relative_to(self.root))

        doc = ScriboDocument(
            slug=slug,
            path=rel_path,
            frontmatter=fm,
            body=body,
            word_count=word_count(body),
        )

        # Update structure
        self._add_to_structure(container_slug, slug)

        return doc

    def delete_document(self, scene_slug: str) -> bool:
        """Remove .scribo file and update _structure.yaml."""
        scribo_file = self._find_scribo_file(scene_slug)
        if not scribo_file:
            return False

        scribo_file.unlink()

        # Remove luna notes sidecar
        notes_file = scribo_file.parent / f".{scene_slug}.notes.yaml"
        if notes_file.exists():
            notes_file.unlink()

        # Update structure
        self._remove_from_structure(scene_slug)

        return True

    def move_document(
        self, scene_slug: str, new_container: str, position: int = -1
    ) -> bool:
        """Move a scene to a different container. Update _structure.yaml."""
        scribo_file = self._find_scribo_file(scene_slug)
        if not scribo_file:
            return False

        # Find target container
        target_dir = self._find_container_dir(new_container)
        if not target_dir:
            target_dir = self.story_dir / new_container
            target_dir.mkdir(parents=True, exist_ok=True)

        # Move file
        new_path = target_dir / scribo_file.name
        scribo_file.rename(new_path)

        # Update frontmatter
        text = new_path.read_text(encoding="utf-8")
        fm, body = parse_scribo(text)
        fm.container = new_container
        content = serialize_scribo(fm, body)
        new_path.write_text(content, encoding="utf-8")

        # Update structure
        self._remove_from_structure(scene_slug)
        self._add_to_structure(new_container, scene_slug, position)

        return True

    def list_documents(
        self, container_slug: Optional[str] = None
    ) -> List[ScriboDocument]:
        """List all .scribo documents, optionally filtered by container."""
        docs = []

        if container_slug:
            search_dir = self._find_container_dir(container_slug)
            if not search_dir:
                return []
        else:
            search_dir = self.story_dir

        if not search_dir or not search_dir.exists():
            return []

        for scribo_file in search_dir.rglob("*.scribo"):
            try:
                text = scribo_file.read_text(encoding="utf-8")
                fm, body = parse_scribo(text)
                wc = word_count(body)
                slug = scribo_file.stem
                rel_path = str(scribo_file.relative_to(self.root))

                docs.append(ScriboDocument(
                    slug=slug,
                    path=rel_path,
                    frontmatter=fm,
                    body=body,
                    word_count=wc,
                ))
            except Exception:
                continue

        return docs

    # =========================================================================
    # Container CRUD
    # =========================================================================

    def create_container(
        self, parent_slug: Optional[str], title: str, level: str
    ) -> ContainerMeta:
        """Create a new container directory with _meta.yaml."""
        slug = slugify(title)

        if parent_slug:
            parent_dir = self._find_container_dir(parent_slug)
            if not parent_dir:
                parent_dir = self.story_dir / parent_slug
        else:
            parent_dir = self.story_dir

        container_dir = parent_dir / slug
        container_dir.mkdir(parents=True, exist_ok=True)

        meta = ContainerMeta(
            title=title,
            slug=slug,
            level=level,
            status="idea",
        )

        meta_file = container_dir / "_meta.yaml"
        data = {
            "title": meta.title,
            "slug": meta.slug,
            "level": meta.level,
            "status": meta.status,
        }
        with open(meta_file, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        # Add to structure
        structure = self.get_structure()
        if parent_slug:
            self._insert_container_in_order(structure.order, parent_slug, slug)
        else:
            if isinstance(structure.order, dict):
                structure.order[slug] = []
            elif isinstance(structure.order, list):
                structure.order.append({slug: []})
            else:
                structure.order = {slug: []}
        self.save_structure(structure)

        return meta

    def get_container(self, container_slug: str) -> Optional[ContainerMeta]:
        """Read container _meta.yaml."""
        container_dir = self._find_container_dir(container_slug)
        if not container_dir:
            return None
        return self._load_meta(container_dir)

    def update_container(self, container_slug: str, updates: Dict[str, Any]) -> Optional[ContainerMeta]:
        """Update container _meta.yaml."""
        container_dir = self._find_container_dir(container_slug)
        if not container_dir:
            return None

        meta = self._load_meta(container_dir)
        if not meta:
            return None

        if "title" in updates:
            meta.title = updates["title"]
        if "status" in updates:
            meta.status = updates["status"]
        if "summary" in updates:
            meta.summary = updates["summary"]
        if "notes" in updates:
            meta.notes = updates["notes"]

        meta_file = container_dir / "_meta.yaml"
        data = {
            "title": meta.title,
            "slug": meta.slug,
            "level": meta.level,
            "status": meta.status,
        }
        if meta.summary:
            data["summary"] = meta.summary
        if meta.notes:
            data["notes"] = meta.notes

        with open(meta_file, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        return meta

    # =========================================================================
    # Luna Notes
    # =========================================================================

    def get_luna_notes(self, scene_slug: str) -> List[LunaNote]:
        """Read Luna's annotations for a scene."""
        return self._load_luna_notes(scene_slug)

    def add_luna_note(self, scene_slug: str, note: LunaNote) -> List[LunaNote]:
        """Append a Luna note to a scene."""
        notes = self._load_luna_notes(scene_slug)
        if note.created is None:
            note.created = datetime.now()
        notes.append(note)
        self._save_luna_notes(scene_slug, notes)
        return notes

    def _load_luna_notes(self, scene_slug: str) -> List[LunaNote]:
        """Load luna notes from sidecar file."""
        scribo_file = self._find_scribo_file(scene_slug)
        if not scribo_file:
            return []

        notes_file = scribo_file.parent / f".{scene_slug}.notes.yaml"
        if not notes_file.exists():
            return []

        try:
            with open(notes_file, "r") as f:
                data = yaml.safe_load(f)
            if not data or not isinstance(data, list):
                return []
            return [
                LunaNote(
                    type=n.get("type", "continuity"),
                    text=n.get("text", ""),
                    line_ref=n.get("line_ref"),
                    created=datetime.fromisoformat(n["created"]) if n.get("created") else None,
                )
                for n in data
            ]
        except Exception:
            return []

    def _save_luna_notes(self, scene_slug: str, notes: List[LunaNote]) -> None:
        """Save luna notes to sidecar file."""
        scribo_file = self._find_scribo_file(scene_slug)
        if not scribo_file:
            return

        notes_file = scribo_file.parent / f".{scene_slug}.notes.yaml"
        data = [
            {
                "type": n.type,
                "text": n.text,
                "line_ref": n.line_ref,
                "created": n.created.isoformat() if n.created else None,
            }
            for n in notes
        ]
        with open(notes_file, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # =========================================================================
    # Search
    # =========================================================================

    def search(self, query: str) -> List[ScriboDocument]:
        """Full-text search across all .scribo body content."""
        query_lower = query.lower()
        results = []

        if not self.story_dir.exists():
            return results

        for scribo_file in self.story_dir.rglob("*.scribo"):
            try:
                text = scribo_file.read_text(encoding="utf-8")
                if query_lower in text.lower():
                    fm, body = parse_scribo(text)
                    wc = word_count(body)
                    rel_path = str(scribo_file.relative_to(self.root))
                    results.append(ScriboDocument(
                        slug=scribo_file.stem,
                        path=rel_path,
                        frontmatter=fm,
                        body=body,
                        word_count=wc,
                    ))
            except Exception:
                continue

        return results

    # =========================================================================
    # Stats
    # =========================================================================

    def get_word_counts(self) -> Dict[str, int]:
        """Word counts per container and total."""
        tree = self.build_story_tree()
        counts = {"total": tree.word_count}

        for child in tree.children:
            counts[child.id] = child.word_count

        return counts

    def get_stats(self) -> Dict[str, Any]:
        """Full stats: word counts, document count, status breakdown."""
        docs = self.list_documents()
        total_words = sum(d.word_count for d in docs)
        status_counts: Dict[str, int] = {}
        for d in docs:
            s = d.frontmatter.status
            status_counts[s] = status_counts.get(s, 0) + 1

        return {
            "document_count": len(docs),
            "total_words": total_words,
            "status_breakdown": status_counts,
            "word_counts": self.get_word_counts(),
        }

    # =========================================================================
    # Character Extraction
    # =========================================================================

    def extract_characters(self, doc: ScriboDocument) -> List[str]:
        """
        Parse body for Fountain-style character names.
        Returns sorted, deduplicated list.
        """
        elements = extract_fountain_elements(doc.body)
        return elements["characters"]

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _find_scribo_file(self, slug: str) -> Optional[Path]:
        """Find a .scribo file by slug anywhere in story/."""
        if not self.story_dir.exists():
            return None

        # Exact match first
        for f in self.story_dir.rglob(f"{slug}.scribo"):
            return f

        # Prefix match (e.g., sc_01_crooked_nail for slug=sc_01)
        for f in self.story_dir.rglob(f"{slug}_*.scribo"):
            return f

        return None

    def _find_container_dir(self, container_slug: str) -> Optional[Path]:
        """Find a container directory by slug anywhere in story/."""
        if not self.story_dir.exists():
            return None

        # Direct child
        direct = self.story_dir / container_slug
        if direct.is_dir():
            return direct

        # Recursive search
        for d in self.story_dir.rglob(container_slug):
            if d.is_dir():
                return d

        return None

    def _add_to_structure(self, container_slug: str, scene_slug: str, position: int = -1) -> None:
        """Add a scene slug to _structure.yaml under a container."""
        structure = self.get_structure()

        def _insert(order: Any, target: str, slug: str) -> bool:
            if isinstance(order, dict):
                if target in order:
                    if isinstance(order[target], list):
                        if position >= 0:
                            order[target].insert(position, slug)
                        else:
                            order[target].append(slug)
                        return True
                for key, val in order.items():
                    if _insert(val, target, slug):
                        return True
            elif isinstance(order, list):
                for item in order:
                    if isinstance(item, dict):
                        if _insert(item, target, slug):
                            return True
            return False

        _insert(structure.order, container_slug, scene_slug)
        self.save_structure(structure)

    def _remove_from_structure(self, scene_slug: str) -> None:
        """Remove a scene slug from _structure.yaml."""
        structure = self.get_structure()

        def _remove(order: Any, slug: str) -> bool:
            if isinstance(order, list):
                if slug in order:
                    order.remove(slug)
                    return True
                for item in order:
                    if isinstance(item, dict):
                        if _remove(item, slug):
                            return True
            elif isinstance(order, dict):
                for key, val in order.items():
                    if _remove(val, slug):
                        return True
            return False

        _remove(structure.order, scene_slug)
        self.save_structure(structure)

    def _insert_container_in_order(
        self, order: Any, parent_slug: str, child_slug: str
    ) -> bool:
        """Insert a container under its parent in the order dict."""
        if isinstance(order, dict):
            if parent_slug in order:
                if isinstance(order[parent_slug], list):
                    order[parent_slug].append({child_slug: []})
                    return True
            for key, val in order.items():
                if self._insert_container_in_order(val, parent_slug, child_slug):
                    return True
        elif isinstance(order, list):
            for item in order:
                if isinstance(item, dict):
                    if self._insert_container_in_order(item, parent_slug, child_slug):
                        return True
        return False


# =============================================================================
# Module-level helper for routes
# =============================================================================


def init_story_directory(project_root: Path) -> None:
    """
    Initialize story/ directory in a project with default _structure.yaml.
    Called during project creation.
    """
    story_dir = project_root / "story"
    story_dir.mkdir(exist_ok=True)

    structure_file = story_dir / "_structure.yaml"
    if not structure_file.exists():
        default = {
            "levels": [
                {"name": "Act", "slug_prefix": "act_"},
                {"name": "Chapter", "slug_prefix": "ch_"},
                {"name": "Scene", "slug_prefix": "sc_"},
            ],
            "order": {},
        }
        with open(structure_file, "w") as f:
            yaml.dump(default, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
