"""
KOZMO Project Operations

Project-level orchestration: directory management, manifest YAML, entity CRUD,
template loading, graph DB initialization.

This module KNOWS about directories and filesystem structure.
It calls entity.py for entity-level operations.

Responsibilities:
- Create/load/save/delete projects
- Manage project manifest YAML
- Entity CRUD within project context
- Load templates from templates/ directory
- Initialize per-project graph database
- Fountain script integration
- Path resolution and validation

Project Directory Structure:
projects/
└── {project_slug}/
    ├── manifest.yaml       # ProjectManifest
    ├── entities/
    │   ├── characters/
    │   ├── locations/
    │   ├── props/
    │   ├── events/
    │   └── lore/
    ├── templates/          # Entity templates
    ├── scripts/            # Fountain scripts
    ├── shots/              # Shot configs
    ├── assets/             # Generated assets
    └── {project_slug}.db   # Graph database
"""

import yaml
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

from .types import (
    ProjectManifest,
    ProjectSettings,
    EdenSettings,
    Entity,
    Template,
    TemplateSection,
    TemplateField,
    EntityLoadResult,
)
from .entity import (
    parse_entity,
    parse_entity_safe,
    save_entity,
    slugify,
)


# =============================================================================
# Project Paths
# =============================================================================


class ProjectPaths:
    """
    Centralized path management for a KOZMO project.

    All paths are relative to project root directory.
    """

    def __init__(self, root: Path):
        self.root = root
        self.manifest = root / "manifest.yaml"
        self.entities = root / "entities"
        self.templates = root / "templates"
        self.scripts = root / "scripts"
        self.shots = root / "shots"
        self.assets = root / "assets"
        self.graph_db = root / f"{root.name}.db"

    def entity_dir(self, entity_type: str) -> Path:
        """Get directory for entity type (e.g., 'characters')."""
        return self.entities / entity_type

    def entity_file(self, entity_type: str, slug: str) -> Path:
        """Get YAML file path for entity."""
        return self.entity_dir(entity_type) / f"{slug}.yaml"

    def template_file(self, entity_type: str) -> Path:
        """Get template file for entity type."""
        return self.templates / f"{entity_type}.yaml"


# =============================================================================
# Project Creation
# =============================================================================


def create_project(
    projects_root: Path,
    name: str,
    slug: Optional[str] = None,
    settings: Optional[ProjectSettings] = None,
    eden: Optional[EdenSettings] = None,
) -> ProjectManifest:
    """
    Create new KOZMO project with directory structure.

    Args:
        projects_root: Root directory for all projects
        name: Human-readable project name
        slug: Filesystem-safe slug (auto-generated if not provided)
        settings: Optional project settings
        eden: Optional Eden integration settings

    Returns:
        ProjectManifest for the new project

    Raises:
        FileExistsError: If project with this slug already exists
    """
    # Generate slug if not provided
    if slug is None:
        slug = slugify(name)

    project_root = projects_root / slug

    # Check if project already exists
    if project_root.exists():
        raise FileExistsError(f"Project already exists: {slug}")

    # Create directory structure
    paths = ProjectPaths(project_root)

    project_root.mkdir(parents=True)
    paths.entities.mkdir()
    paths.templates.mkdir()
    paths.scripts.mkdir()
    paths.shots.mkdir()
    paths.assets.mkdir()

    # Create entity type subdirectories
    for entity_type in ["characters", "locations", "props", "events", "lore"]:
        (paths.entities / entity_type).mkdir()

    # Create LAB pipeline directories
    (project_root / "lab" / "briefs").mkdir(parents=True, exist_ok=True)
    (project_root / "lab" / "assets").mkdir(parents=True, exist_ok=True)

    # Create story directory with default structure
    from .scribo import init_story_directory
    init_story_directory(project_root)

    # Create manifest
    now = datetime.now()
    manifest = ProjectManifest(
        name=name,
        slug=slug,
        version=1,
        created=now,
        updated=now,
        settings=settings or ProjectSettings(),
        eden=eden or EdenSettings(),
    )

    # Save manifest
    save_manifest(paths, manifest)

    return manifest


# =============================================================================
# Project Loading
# =============================================================================


def load_project(projects_root: Path, slug: str) -> Optional[ProjectManifest]:
    """
    Load project manifest from YAML.

    Args:
        projects_root: Root directory for all projects
        slug: Project slug

    Returns:
        ProjectManifest if found, None otherwise
    """
    project_root = projects_root / slug
    paths = ProjectPaths(project_root)

    if not paths.manifest.exists():
        return None

    with open(paths.manifest, "r") as f:
        data = yaml.safe_load(f)

    # Parse dates
    created = datetime.fromisoformat(data["created"])
    updated = datetime.fromisoformat(data["updated"])

    # Default entity types
    default_entity_types = ["characters", "locations", "props", "events", "lore"]

    # Build manifest
    manifest = ProjectManifest(
        name=data["name"],
        slug=data["slug"],
        version=data.get("version", 1),
        created=created,
        updated=updated,
        entity_types=data.get("entity_types", default_entity_types),
        settings=ProjectSettings(**data.get("settings", {})),
        eden=EdenSettings(**data.get("eden", {})) if "eden" in data else None,
    )

    return manifest


# =============================================================================
# Project Saving
# =============================================================================


def save_manifest(paths: ProjectPaths, manifest: ProjectManifest) -> None:
    """
    Save project manifest to YAML.

    Args:
        paths: Project paths
        manifest: Manifest to save
    """
    # Update timestamp
    manifest.updated = datetime.now()

    # Serialize to dict
    data = {
        "name": manifest.name,
        "slug": manifest.slug,
        "version": manifest.version,
        "created": manifest.created.isoformat(),
        "updated": manifest.updated.isoformat(),
        "entity_types": manifest.entity_types,
        "settings": manifest.settings.model_dump(),
    }

    if manifest.eden:
        data["eden"] = manifest.eden.model_dump()

    # Write YAML
    with open(paths.manifest, "w") as f:
        yaml.dump(
            data,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )


# =============================================================================
# Entity CRUD
# =============================================================================


def load_entity(
    paths: ProjectPaths,
    entity_type: str,
    slug: str,
    template: Optional[Template] = None,
) -> EntityLoadResult:
    """
    Load entity from project.

    Args:
        paths: Project paths
        entity_type: Entity type (e.g., 'characters')
        slug: Entity slug
        template: Optional template for validation

    Returns:
        EntityLoadResult with entity, warnings, and/or error
    """
    entity_file = paths.entity_file(entity_type, slug)
    return parse_entity_safe(entity_file, template)


def save_entity_to_project(
    paths: ProjectPaths,
    entity: Entity,
) -> None:
    """
    Save entity to project.

    Args:
        paths: Project paths
        entity: Entity to save
    """
    # Ensure entity type directory exists
    entity_dir = paths.entity_dir(entity.type)
    entity_dir.mkdir(parents=True, exist_ok=True)

    # Save entity
    entity_file = paths.entity_file(entity.type, entity.slug)
    save_entity(entity, entity_file)


def list_entities(
    paths: ProjectPaths,
    entity_type: Optional[str] = None,
) -> List[str]:
    """
    List entity slugs in project.

    Args:
        paths: Project paths
        entity_type: Optional entity type to filter (e.g., 'characters')
                    If None, lists all entities

    Returns:
        List of entity slugs
    """
    if entity_type:
        entity_dir = paths.entity_dir(entity_type)
        if not entity_dir.exists():
            return []
        return [f.stem for f in entity_dir.glob("*.yaml")]
    else:
        # List all entities across all types
        slugs = []
        for type_dir in paths.entities.iterdir():
            if type_dir.is_dir():
                slugs.extend([f.stem for f in type_dir.glob("*.yaml")])
        return slugs


def delete_entity(
    paths: ProjectPaths,
    entity_type: str,
    slug: str,
) -> bool:
    """
    Delete entity from project.

    Args:
        paths: Project paths
        entity_type: Entity type
        slug: Entity slug

    Returns:
        True if deleted, False if not found
    """
    entity_file = paths.entity_file(entity_type, slug)
    if entity_file.exists():
        entity_file.unlink()
        return True
    return False


# =============================================================================
# Template Loading
# =============================================================================


def load_template(paths: ProjectPaths, entity_type: str) -> Optional[Template]:
    """
    Load entity template from project templates/ directory.

    Args:
        paths: Project paths
        entity_type: Entity type (e.g., 'character')

    Returns:
        Template if found, None otherwise
    """
    template_file = paths.template_file(entity_type)

    if not template_file.exists():
        return None

    with open(template_file, "r") as f:
        data = yaml.safe_load(f)

    # Parse sections
    sections = []
    for section_data in data.get("sections", []):
        fields = []
        for field_data in section_data.get("fields", []):
            fields.append(
                TemplateField(
                    key=field_data["key"],
                    type=field_data["type"],
                    required=field_data.get("required", False),
                    description=field_data.get("description"),
                    options=field_data.get("options"),
                )
            )
        sections.append(
            TemplateSection(
                name=section_data["name"],
                dynamic=section_data.get("dynamic", True),
                fields=fields,
            )
        )

    template = Template(
        type=data["type"],
        version=data.get("version", 1),
        sections=sections,
    )

    return template


def save_template(paths: ProjectPaths, template: Template) -> None:
    """
    Save entity template to project templates/ directory.

    Args:
        paths: Project paths
        template: Template to save
    """
    template_file = paths.template_file(template.type)

    # Serialize template
    data = {
        "type": template.type,
        "version": template.version,
        "sections": [
            {
                "name": section.name,
                "dynamic": section.dynamic,
                "fields": [
                    {
                        "key": field.key,
                        "type": field.type,
                        "required": field.required,
                        "description": field.description,
                        "options": field.options,
                    }
                    for field in section.fields
                ],
            }
            for section in template.sections
        ],
    }

    # Write YAML
    with open(template_file, "w") as f:
        yaml.dump(
            data,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )


# =============================================================================
# Project Deletion
# =============================================================================


def delete_project(projects_root: Path, slug: str) -> bool:
    """
    Delete project and all its data.

    WARNING: This is destructive and cannot be undone.

    Args:
        projects_root: Root directory for all projects
        slug: Project slug

    Returns:
        True if deleted, False if not found
    """
    project_root = projects_root / slug

    if not project_root.exists():
        return False

    # Delete all files and directories
    import shutil
    shutil.rmtree(project_root)

    return True


# =============================================================================
# Project Listing
# =============================================================================


def list_projects(projects_root: Path) -> List[str]:
    """
    List all project slugs.

    Args:
        projects_root: Root directory for all projects

    Returns:
        List of project slugs
    """
    if not projects_root.exists():
        return []

    return [
        d.name
        for d in projects_root.iterdir()
        if d.is_dir() and (d / "manifest.yaml").exists()
    ]
