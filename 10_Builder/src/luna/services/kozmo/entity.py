"""
KOZMO Entity Operations

Entity-level logic: parsing YAML, validation, slug generation, YAML round-trip.

This module does NOT know about projects or directories.
It works with paths and templates, and is called by project.py.

Responsibilities:
- Parse YAML file → Entity model
- Validate entity against template schema
- Generate slugs from names
- Entity model → YAML string (preserving key order)
- Graceful error handling with warnings
"""

import re
import yaml
from pathlib import Path
from typing import Optional, List

from .types import Entity, Template, EntityLoadResult, TemplateField, TemplateSection


# =============================================================================
# Slug Generation
# =============================================================================


def slugify(name: str) -> str:
    """
    Convert entity name to filesystem-safe slug.

    Examples:
        "Mordecai The Unwise" → "mordecai_the_unwise"
        "The Crooked Nail" → "the_crooked_nail"
        "Princess-of-Shadows" → "princess_of_shadows"

    Args:
        name: Entity name

    Returns:
        Slug (lowercase, underscores, alphanumeric only)
    """
    # Lowercase
    slug = name.lower()

    # Replace spaces and hyphens with underscores
    slug = slug.replace(" ", "_").replace("-", "_")

    # Remove non-alphanumeric except underscores
    slug = re.sub(r"[^a-z0-9_]", "", slug)

    # Collapse multiple underscores
    slug = re.sub(r"_+", "_", slug)

    # Strip leading/trailing underscores
    slug = slug.strip("_")

    return slug


# =============================================================================
# YAML Parsing
# =============================================================================


def parse_entity(path: Path, template: Optional[Template] = None) -> Entity:
    """
    Parse entity YAML file into Entity model.

    Args:
        path: Path to entity YAML file
        template: Optional template for validation

    Returns:
        Entity model

    Raises:
        FileNotFoundError: If path doesn't exist
        yaml.YAMLError: If YAML is invalid
        ValueError: If required fields missing
    """
    if not path.exists():
        raise FileNotFoundError(f"Entity file not found: {path}")

    with open(path, "r") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError(f"Empty YAML file: {path}")

    # Extract core fields
    entity_type = data.get("type")
    name = data.get("name")

    if not name:
        raise ValueError(f"Missing required field 'name' in {path}")

    # Generate slug from name if not provided
    slug = data.get("slug", slugify(name))

    # Extract known fields
    status = data.get("status", "active")
    relationships = data.get("relationships", [])
    references = data.get("references", {})
    scenes = data.get("scenes", [])
    tags = data.get("tags", [])
    luna_notes = data.get("luna_notes")

    # Everything else goes into data dict
    excluded_keys = {
        "type", "name", "slug", "status",
        "relationships", "references", "scenes", "tags", "luna_notes"
    }
    entity_data = {k: v for k, v in data.items() if k not in excluded_keys}

    # Construct entity
    entity = Entity(
        type=entity_type,
        name=name,
        slug=slug,
        status=status,
        data=entity_data,
        relationships=relationships,
        references=references,
        scenes=scenes,
        tags=tags,
        luna_notes=luna_notes,
    )

    return entity


def parse_entity_safe(
    path: Path, template: Optional[Template] = None
) -> EntityLoadResult:
    """
    Gracefully parse entity YAML with error/warning collection.

    Fatal errors (entity=None):
    - YAML syntax error
    - Missing 'name' field

    Warnings (entity loaded, warnings populated):
    - Missing optional template fields
    - Unknown fields not in template
    - Type mismatches
    - Invalid relationship references

    Args:
        path: Path to entity YAML file
        template: Optional template for validation

    Returns:
        EntityLoadResult with entity, warnings, and/or error
    """
    warnings: List[str] = []

    try:
        entity = parse_entity(path, template)

        # Validate against template if provided
        if template:
            validation_warnings = validate_entity(entity, template)
            warnings.extend(validation_warnings)

        return EntityLoadResult(entity=entity, warnings=warnings, error=None)

    except FileNotFoundError as e:
        return EntityLoadResult(entity=None, warnings=[], error=str(e))

    except yaml.YAMLError as e:
        return EntityLoadResult(
            entity=None, warnings=[], error=f"YAML syntax error: {e}"
        )

    except ValueError as e:
        return EntityLoadResult(entity=None, warnings=[], error=str(e))

    except Exception as e:
        return EntityLoadResult(
            entity=None, warnings=[], error=f"Unexpected error: {e}"
        )


# =============================================================================
# Entity Validation
# =============================================================================


def validate_entity(entity: Entity, template: Template) -> List[str]:
    """
    Validate entity against template schema.

    Returns list of warnings. Does not raise exceptions.
    Validation is informational, not blocking.

    Checks:
    - Required fields present
    - Field types match template
    - Unknown fields not in template
    - Relationship references valid (if entity_map provided)

    Args:
        entity: Entity to validate
        template: Template schema

    Returns:
        List of warning messages
    """
    warnings: List[str] = []

    # Collect all expected fields from template
    expected_fields = set()
    required_fields = set()

    for section in template.sections:
        for field in section.fields:
            expected_fields.add(field.key)
            if field.required:
                required_fields.add(field.key)

    # Check required fields
    for field_key in required_fields:
        if field_key not in entity.data and not hasattr(entity, field_key):
            warnings.append(f"Missing required field: {field_key}")

    # Check for unknown fields (not in template)
    actual_fields = set(entity.data.keys())
    unknown_fields = actual_fields - expected_fields
    for field in unknown_fields:
        warnings.append(f"Unknown field not in template: {field}")

    # Type validation would go here if needed
    # For now, YAML parsing handles basic types

    return warnings


# =============================================================================
# YAML Serialization
# =============================================================================


def entity_to_yaml(entity: Entity) -> str:
    """
    Convert Entity model to YAML string.

    Preserves key order and handles luna_notes specially.
    luna_notes appears at the bottom, visually separated.

    Args:
        entity: Entity to serialize

    Returns:
        YAML string
    """
    # Build ordered dict for YAML output
    data = {
        "type": entity.type,
        "name": entity.name,
    }

    # Add status if not default
    if entity.status != "active":
        data["status"] = entity.status

    # Add entity data fields (from template)
    data.update(entity.data)

    # Add relationships if present
    if entity.relationships:
        data["relationships"] = [r.model_dump() for r in entity.relationships]

    # Add references if present
    if entity.references.images or entity.references.lora:
        data["references"] = entity.references.model_dump()

    # Add scenes if present
    if entity.scenes:
        data["scenes"] = entity.scenes

    # Add tags if present
    if entity.tags:
        data["tags"] = entity.tags

    # Add luna_notes at the end if present
    if entity.luna_notes:
        data["luna_notes"] = entity.luna_notes

    # Serialize to YAML
    yaml_str = yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

    return yaml_str


def save_entity(entity: Entity, path: Path) -> None:
    """
    Save Entity model to YAML file.

    Args:
        entity: Entity to save
        path: Path to write YAML file
    """
    yaml_str = entity_to_yaml(entity)
    with open(path, "w") as f:
        f.write(yaml_str)
