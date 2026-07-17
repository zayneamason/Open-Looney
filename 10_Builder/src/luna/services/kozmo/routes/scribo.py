"""KOZMO SCRIBO Routes — Story tree, documents, containers, notes, search."""

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..types import LunaNote, StoryStructure
from . import _get_scribo_service

router = APIRouter(tags=["kozmo-scribo"])


class CreateDocumentRequest(BaseModel):
    container: str
    title: str
    type: str = "scene"


class UpdateDocumentRequest(BaseModel):
    frontmatter: Optional[dict] = None
    body: Optional[str] = None


class MoveDocumentRequest(BaseModel):
    new_container: str
    position: int = -1


class CreateContainerRequest(BaseModel):
    parent: Optional[str] = None
    title: str
    level: str


class UpdateContainerRequest(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None
    notes: Optional[str] = None


class AddLunaNoteRequest(BaseModel):
    type: str  # continuity | tone | thematic | production | character
    text: str
    line_ref: Optional[int] = None


# ── Story Tree ────────────────────────────────────────────────────────────────


@router.get("/projects/{project_slug}/story")
def api_story_tree(project_slug: str):
    """Get story tree (recursive hierarchy with word counts)."""
    svc = _get_scribo_service(project_slug)
    tree = svc.build_story_tree()
    return tree.model_dump()


@router.get("/projects/{project_slug}/story/structure")
def api_story_structure(project_slug: str):
    """Get story hierarchy definition + ordering."""
    svc = _get_scribo_service(project_slug)
    structure = svc.get_structure()
    return structure.model_dump()


@router.put("/projects/{project_slug}/story/structure")
def api_update_story_structure(project_slug: str, structure: StoryStructure):
    """Update story ordering (drag-and-drop reorder)."""
    svc = _get_scribo_service(project_slug)
    svc.save_structure(structure)
    return {"updated": True}


# ── Containers ────────────────────────────────────────────────────────────────


@router.post("/projects/{project_slug}/story/containers", status_code=201)
def api_create_container(project_slug: str, req: CreateContainerRequest):
    """Create a new story container (act, chapter, etc.)."""
    svc = _get_scribo_service(project_slug)
    meta = svc.create_container(req.parent, req.title, req.level)
    return meta.model_dump()


@router.get("/projects/{project_slug}/story/containers/{container_slug}")
def api_get_container(project_slug: str, container_slug: str):
    """Get container metadata."""
    svc = _get_scribo_service(project_slug)
    meta = svc.get_container(container_slug)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Container not found: {container_slug}")
    return meta.model_dump()


@router.put("/projects/{project_slug}/story/containers/{container_slug}")
def api_update_container(project_slug: str, container_slug: str, req: UpdateContainerRequest):
    """Update container metadata."""
    svc = _get_scribo_service(project_slug)
    meta = svc.update_container(container_slug, req.model_dump(exclude_none=True))
    if not meta:
        raise HTTPException(status_code=404, detail=f"Container not found: {container_slug}")
    return meta.model_dump()


# ── Documents ─────────────────────────────────────────────────────────────────


@router.get("/projects/{project_slug}/story/documents")
def api_list_documents(project_slug: str, container: Optional[str] = None):
    """List all .scribo documents, optionally filtered by container."""
    svc = _get_scribo_service(project_slug)
    docs = svc.list_documents(container)
    return {
        "documents": [
            {
                "slug": d.slug,
                "path": d.path,
                "type": d.frontmatter.type,
                "container": d.frontmatter.container,
                "status": d.frontmatter.status,
                "word_count": d.word_count,
                "characters_present": d.frontmatter.characters_present,
                "location": d.frontmatter.location,
                "audio_file": d.frontmatter.audio_file,
                "audio_duration": d.frontmatter.audio_duration,
                "time": d.frontmatter.time,
            }
            for d in docs
        ],
        "count": len(docs),
    }


@router.get("/projects/{project_slug}/story/documents/{doc_slug}")
def api_get_document(project_slug: str, doc_slug: str):
    """Get a single SCRIBO document (frontmatter + body)."""
    svc = _get_scribo_service(project_slug)
    doc = svc.get_document(doc_slug)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_slug}")
    return doc.model_dump(mode="json")


@router.post("/projects/{project_slug}/story/documents", status_code=201)
def api_create_document(project_slug: str, req: CreateDocumentRequest):
    """Create a new .scribo document."""
    svc = _get_scribo_service(project_slug)
    doc = svc.create_document(req.container, req.title, req.type)
    return {
        "slug": doc.slug,
        "path": doc.path,
        "status": doc.frontmatter.status,
    }


@router.put("/projects/{project_slug}/story/documents/{doc_slug}")
def api_update_document(project_slug: str, doc_slug: str, req: UpdateDocumentRequest):
    """Update a .scribo document (frontmatter and/or body)."""
    svc = _get_scribo_service(project_slug)
    doc = svc.get_document(doc_slug)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_slug}")

    if req.frontmatter is not None:
        from ..types import ScriboFrontmatter
        doc.frontmatter = ScriboFrontmatter(**{**doc.frontmatter.model_dump(), **req.frontmatter})
    if req.body is not None:
        doc.body = req.body

    doc = svc.save_document(doc)
    return {"updated": doc_slug, "word_count": doc.word_count}


@router.delete("/projects/{project_slug}/story/documents/{doc_slug}")
def api_delete_document(project_slug: str, doc_slug: str):
    """Delete a .scribo document."""
    svc = _get_scribo_service(project_slug)
    deleted = svc.delete_document(doc_slug)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_slug}")
    return {"deleted": doc_slug}


@router.post("/projects/{project_slug}/story/documents/{doc_slug}/move")
def api_move_document(project_slug: str, doc_slug: str, req: MoveDocumentRequest):
    """Move a document to a different container."""
    svc = _get_scribo_service(project_slug)
    moved = svc.move_document(doc_slug, req.new_container, req.position)
    if not moved:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_slug}")
    return {"moved": doc_slug, "new_container": req.new_container}


# ── Luna Notes ────────────────────────────────────────────────────────────────


@router.get("/projects/{project_slug}/story/documents/{doc_slug}/notes")
def api_get_luna_notes(project_slug: str, doc_slug: str):
    """Get Luna's notes for a scene."""
    svc = _get_scribo_service(project_slug)
    notes = svc.get_luna_notes(doc_slug)
    return {"notes": [n.model_dump(mode="json") for n in notes]}


@router.post("/projects/{project_slug}/story/documents/{doc_slug}/notes", status_code=201)
def api_add_luna_note(project_slug: str, doc_slug: str, req: AddLunaNoteRequest):
    """Add a Luna note to a scene (write boundary: only Luna writes here)."""
    svc = _get_scribo_service(project_slug)
    note = LunaNote(type=req.type, text=req.text, line_ref=req.line_ref)
    notes = svc.add_luna_note(doc_slug, note)
    return {"notes": [n.model_dump(mode="json") for n in notes]}


# ── Search & Stats ────────────────────────────────────────────────────────────


@router.get("/projects/{project_slug}/story/search")
def api_story_search(project_slug: str, q: str = ""):
    """Full-text search across all .scribo content."""
    if not q.strip():
        return {"results": [], "count": 0}
    svc = _get_scribo_service(project_slug)
    results = svc.search(q)
    return {
        "results": [
            {
                "slug": d.slug,
                "path": d.path,
                "type": d.frontmatter.type,
                "status": d.frontmatter.status,
                "word_count": d.word_count,
            }
            for d in results
        ],
        "count": len(results),
    }


@router.get("/projects/{project_slug}/story/stats")
def api_story_stats(project_slug: str):
    """Word counts per container, total, status breakdown."""
    svc = _get_scribo_service(project_slug)
    return svc.get_stats()
