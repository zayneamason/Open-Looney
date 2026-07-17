"""
Settings API Routes
====================
GET/POST endpoints for each settings section.
Reads/writes existing config files — no new storage layer.
"""

import json
import os
import logging
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from luna.core.paths import project_root, config_dir

logger = logging.getLogger("luna.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Resolve config root relative to project root
_PROJECT_ROOT = project_root()
CONFIG_ROOT = config_dir()
SECRETS_PATH = CONFIG_ROOT / "secrets.json"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_json(filename: str) -> dict:
    path = CONFIG_ROOT / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Config file not found: {filename}")
    return json.loads(path.read_text())


def _write_json(filename: str, data: dict):
    path = CONFIG_ROOT / filename
    path.write_text(json.dumps(data, indent=2) + "\n")


def _read_yaml(filename: str) -> dict:
    path = CONFIG_ROOT / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Config file not found: {filename}")
    return yaml.safe_load(path.read_text()) or {}


def _write_yaml(filename: str, data: dict):
    path = CONFIG_ROOT / filename
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def _read_secrets() -> dict:
    if SECRETS_PATH.exists():
        return json.loads(SECRETS_PATH.read_text())
    return {}


def _write_secrets(data: dict):
    SECRETS_PATH.write_text(json.dumps(data, indent=2) + "\n")


def _mask_key(key: str) -> str:
    """Mask an API key for display: first 8 + ... + last 4."""
    if not key or len(key) < 16:
        return "••••••••"
    return key[:8] + "..." + key[-4:]


def _decrypt_secret(value: str) -> str:
    """Decrypt a Fernet-encrypted secret value (prefixed with 'enc:').

    Requires LUNA_SECRETS_KEY in the environment. Returns the value unchanged
    if it doesn't start with 'enc:' (backwards-compatible with plaintext keys).
    """
    if not value.startswith("enc:"):
        return value
    secrets_key = os.environ.get("LUNA_SECRETS_KEY", "")
    if not secrets_key:
        logger.error("[SECRETS] LUNA_SECRETS_KEY not set — cannot decrypt secrets")
        return ""
    try:
        from cryptography.fernet import Fernet
        f = Fernet(secrets_key.encode())
        return f.decrypt(value[4:].encode()).decode()
    except Exception as e:
        logger.error(f"[SECRETS] Decryption failed: {e}")
        return ""


def _inject_secrets_to_env():
    """Load secrets.json into os.environ (called at startup).

    Values prefixed with 'enc:' are Fernet-decrypted using LUNA_SECRETS_KEY.
    """
    secrets = _read_secrets()
    for k, v in secrets.items():
        if v and isinstance(v, str):
            decrypted = _decrypt_secret(v)
            if decrypted:  # never overwrite an existing env var with an empty string
                os.environ[k] = decrypted


# ── LLM Settings ────────────────────────────────────────────────────────────

@router.get("/llm")
async def get_llm_settings():
    """Return merged LLM providers config + fallback chain + key status."""
    providers = _read_json("llm_providers.json")
    fallback = _read_yaml("fallback_chain.yaml")
    secrets = _read_secrets()

    # Add key status per provider (masked or empty)
    for pid, pconf in providers.get("providers", {}).items():
        env_var = pconf.get("api_key_env", "")
        raw_key = secrets.get(env_var) or os.environ.get(env_var, "")
        pconf["key_status"] = "set" if raw_key else "not_set"
        pconf["key_masked"] = _mask_key(raw_key) if raw_key else ""

    return {
        "providers": providers,
        "fallback": fallback,
    }


class LLMUpdate(BaseModel):
    current_provider: Optional[str] = None
    provider_toggles: Optional[dict] = None  # {provider_id: bool}
    default_models: Optional[dict] = None    # {provider_id: model_name}
    fallback_chain: Optional[list] = None
    fallback_timeout_ms: Optional[int] = None
    fallback_max_retries: Optional[int] = None
    api_keys: Optional[dict] = None          # {ENV_VAR_NAME: key_value}


@router.post("/llm")
async def update_llm_settings(update: LLMUpdate):
    """Update LLM provider config, fallback chain, and/or API keys."""
    providers = _read_json("llm_providers.json")
    fallback = _read_yaml("fallback_chain.yaml")

    if update.current_provider:
        if update.current_provider not in providers.get("providers", {}):
            raise HTTPException(status_code=422, detail=f"Unknown provider: {update.current_provider}")
        providers["current_provider"] = update.current_provider

    if update.provider_toggles:
        for pid, enabled in update.provider_toggles.items():
            if pid in providers.get("providers", {}):
                providers["providers"][pid]["enabled"] = bool(enabled)

    if update.default_models:
        for pid, model in update.default_models.items():
            if pid in providers.get("providers", {}):
                providers["providers"][pid]["default_model"] = model

    if update.fallback_chain:
        if len(update.fallback_chain) < 1:
            raise HTTPException(status_code=422, detail="Fallback chain must have at least one provider")
        fallback["chain"] = update.fallback_chain

    if update.fallback_timeout_ms is not None:
        fallback["per_provider_timeout_ms"] = max(1000, min(120000, update.fallback_timeout_ms))

    if update.fallback_max_retries is not None:
        fallback["max_retries_per_provider"] = max(0, min(5, update.fallback_max_retries))

    if update.api_keys:
        secrets = _read_secrets()
        for env_var, key_val in update.api_keys.items():
            if key_val and isinstance(key_val, str):
                secrets[env_var] = key_val
                os.environ[env_var] = key_val
            elif key_val == "":
                secrets.pop(env_var, None)
                os.environ.pop(env_var, None)
        _write_secrets(secrets)

    _write_json("llm_providers.json", providers)
    _write_yaml("fallback_chain.yaml", fallback)
    return {"success": True}


@router.post("/llm/test")
async def test_llm_provider(body: dict):
    """Test an API key by sending a tiny inference request."""
    provider = body.get("provider")
    if not provider:
        raise HTTPException(status_code=422, detail="provider is required")

    providers = _read_json("llm_providers.json")
    pconf = providers.get("providers", {}).get(provider)
    if not pconf:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    env_var = pconf.get("api_key_env", "")
    secrets = _read_secrets()
    key = secrets.get(env_var) or os.environ.get(env_var, "")

    if not key:
        return {"success": False, "error": "No API key configured"}

    # Quick connectivity test per provider
    try:
        if provider == "claude":
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": pconf.get("default_model", "claude-haiku-4-5-20251001"),
                        "max_tokens": 5,
                        "messages": [{"role": "user", "content": "Hi"}],
                    },
                )
                if resp.status_code == 200:
                    return {"success": True, "message": "Claude API key is valid"}
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

        elif provider == "groq":
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": pconf.get("default_model", "llama-3.3-70b-versatile"),
                        "messages": [{"role": "user", "content": "Hi"}],
                        "max_tokens": 5,
                    },
                )
                if resp.status_code == 200:
                    return {"success": True, "message": "Groq API key is valid"}
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

        elif provider == "gemini":
            import httpx
            model = pconf.get("default_model", "gemini-2.0-flash")
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                    headers={"Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": "Hi"}]}]},
                )
                if resp.status_code == 200:
                    return {"success": True, "message": "Gemini API key is valid"}
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

        else:
            return {"success": False, "error": f"No test implemented for provider: {provider}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Local Inference Settings ─────────────────────────────────────────────────

_LOCAL_INFERENCE_DEFAULTS = {
    "model": {
        "model_id": "Qwen/Qwen2.5-3B-Instruct",
        "use_4bit": True,
        "cache_prompt": True,
        "adapter_path": "models/luna_lora_mlx",
    },
    "generation": {
        "max_tokens": 512,
        "temperature": 0.7,
        "top_p": 0.9,
        "repetition_penalty": 1.1,
    },
    "performance": {
        "hot_path_timeout_ms": 200,
    },
    "routing": {
        "complexity_threshold": 0.35,
    },
}


@router.get("/local-inference")
async def get_local_inference_settings():
    """Read local inference config."""
    path = CONFIG_ROOT / "local_inference.json"
    if path.exists():
        return json.loads(path.read_text())
    return _LOCAL_INFERENCE_DEFAULTS


class LocalInferenceUpdate(BaseModel):
    model: Optional[dict] = None
    generation: Optional[dict] = None
    performance: Optional[dict] = None
    routing: Optional[dict] = None


@router.post("/local-inference")
async def update_local_inference_settings(update: LocalInferenceUpdate):
    """Update local inference config (deep merge). Requires restart."""
    path = CONFIG_ROOT / "local_inference.json"
    if path.exists():
        current = json.loads(path.read_text())
    else:
        current = dict(_LOCAL_INFERENCE_DEFAULTS)

    # Deep merge each section
    for section in ("model", "generation", "performance", "routing"):
        incoming = getattr(update, section)
        if incoming:
            if section not in current:
                current[section] = {}
            current[section].update(incoming)

    # Validation
    gen = current.get("generation", {})
    if not (0.0 <= gen.get("temperature", 0.7) <= 2.0):
        raise HTTPException(status_code=422, detail="temperature must be 0.0-2.0")
    if not (0.0 <= gen.get("top_p", 0.9) <= 1.0):
        raise HTTPException(status_code=422, detail="top_p must be 0.0-1.0")
    if not (1 <= gen.get("max_tokens", 512) <= 4096):
        raise HTTPException(status_code=422, detail="max_tokens must be 1-4096")
    if not (1.0 <= gen.get("repetition_penalty", 1.1) <= 2.0):
        raise HTTPException(status_code=422, detail="repetition_penalty must be 1.0-2.0")

    perf = current.get("performance", {})
    if not (50 <= perf.get("hot_path_timeout_ms", 200) <= 5000):
        raise HTTPException(status_code=422, detail="hot_path_timeout_ms must be 50-5000")

    routing = current.get("routing", {})
    if not (0.0 <= routing.get("complexity_threshold", 0.35) <= 1.0):
        raise HTTPException(status_code=422, detail="complexity_threshold must be 0.0-1.0")

    _write_json("local_inference.json", current)
    return {"success": True, "restart_required": True}


# ── Identity Settings ────────────────────────────────────────────────────────

@router.get("/identity")
async def get_identity_settings():
    path = CONFIG_ROOT / "identity_bypass.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"entity_id": "", "entity_name": "", "luna_tier": "admin",
            "dataroom_tier": 1, "dataroom_categories": []}


@router.post("/identity")
async def update_identity_settings(update: dict):
    current = _read_json("identity_bypass.json")
    allowed = {"entity_id", "entity_name", "luna_tier", "dataroom_tier", "dataroom_categories"}
    for k, v in update.items():
        if k in allowed:
            current[k] = v
    _write_json("identity_bypass.json", current)
    return {"success": True}


# ── Voice Settings ───────────────────────────────────────────────────────────

@router.get("/voice")
async def get_voice_settings():
    personality = _read_json("personality.json")
    return {
        "expression": personality.get("expression", {}),
    }


@router.post("/voice")
async def update_voice_settings(update: dict):
    personality = _read_json("personality.json")
    if "expression" in update:
        personality["expression"].update(update["expression"])
    _write_json("personality.json", personality)
    return {"success": True}


# ── Personality Settings ─────────────────────────────────────────────────────

@router.get("/personality")
async def get_personality_settings():
    personality = _read_json("personality.json")
    ls_path = CONFIG_ROOT / "lunascript.yaml"
    lunascript = yaml.safe_load(ls_path.read_text()) if ls_path.exists() else {"scripts": []}
    return {
        "personality": personality,
        "lunascript": lunascript,
    }


@router.post("/personality")
async def update_personality_settings(update: dict):
    personality = _read_json("personality.json")

    if "token_budget" in update:
        personality["token_budget"].update(update["token_budget"])
    if "reflection_loop" in update:
        personality["reflection_loop"].update(update["reflection_loop"])
    if "expression" in update:
        personality["expression"].update(update["expression"])

    _write_json("personality.json", personality)

    if "lunascript" in update:
        lunascript = _read_yaml("lunascript.yaml")
        lunascript.update(update["lunascript"])
        _write_yaml("lunascript.yaml", lunascript)

    return {"success": True}


# ── Memory Economy Settings ──────────────────────────────────────────────────

_MEMORY_ECONOMY_DEFAULTS = {
    "weights": {"strength": 0.3, "access_count": 0.25, "edges": 0.2, "age": 0.15, "importance": 0.1},
    "decay": {"crystallized": 0.001, "settled": 0.005, "fluid": 0.02, "drifting": 0.05},
    "thresholds": {"crystallized": 0.85, "settled": 0.5, "fluid": 0.2},
    "clustering": {"min_cluster_size": 3, "merge_threshold": 0.75},
    "retrieval": {"max_results": 20, "min_similarity": 0.3},
}


@router.get("/memory")
async def get_memory_settings():
    path = CONFIG_ROOT / "memory_economy_config.json"
    if path.exists():
        return json.loads(path.read_text())
    return _MEMORY_ECONOMY_DEFAULTS


@router.post("/memory")
async def update_memory_settings(update: dict):
    current = _read_json("memory_economy_config.json")

    # Validate lock-in weights sum to 1.0
    if "weights" in update:
        w = update["weights"]
        total = sum(w.values())
        if abs(total - 1.0) > 0.01:
            raise HTTPException(status_code=422, detail=f"Lock-in weights must sum to 1.0, got {total:.3f}")

    # Deep merge one level
    for key, val in update.items():
        if isinstance(val, dict) and key in current and isinstance(current[key], dict):
            current[key].update(val)
        else:
            current[key] = val

    _write_json("memory_economy_config.json", current)

    # Invalidate the runtime config cache so changes take effect immediately
    try:
        from luna.memory.config_loader import invalidate_cache
        invalidate_cache()
    except ImportError:
        pass

    return {"success": True}


# ── Collections (Aibrarian) ─────────────────────────────────────────────────

@router.get("/collections")
async def get_collections_settings():
    return _read_yaml("aibrarian_registry.yaml")


@router.post("/collections")
async def update_collections_settings(update: dict):
    registry = _read_yaml("aibrarian_registry.yaml")

    if "collections" in update:
        for coll_id, coll_data in update["collections"].items():
            if coll_id in registry.get("collections", {}):
                registry["collections"][coll_id].update(coll_data)
            else:
                registry["collections"][coll_id] = coll_data

    _write_yaml("aibrarian_registry.yaml", registry)
    return {"success": True}


# ── Network Settings ─────────────────────────────────────────────────────────

_NETWORK_DEFAULTS = {
    "services": {
        "backend": {"host": "127.0.0.1", "port": 8000},
        "frontend": {"port": 5173},
        "observatory": {"port": 8100, "enabled": True},
    }
}


@router.get("/network")
async def get_network_settings():
    path = CONFIG_ROOT / "luna.launch.json"
    if path.exists():
        return json.loads(path.read_text())
    return _NETWORK_DEFAULTS


@router.post("/network")
async def update_network_settings(update: dict):
    current = _read_json("luna.launch.json")

    services = update.get("services", {})
    for svc_name, svc_conf in services.items():
        if svc_name in current.get("services", {}):
            # Validate port
            if "port" in svc_conf:
                port = svc_conf["port"]
                if not (1024 <= port <= 65535):
                    raise HTTPException(status_code=422, detail=f"Port must be 1024-65535, got {port}")
            current["services"][svc_name].update(svc_conf)

    _write_json("luna.launch.json", current)
    return {"success": True, "requires_restart": True}


# ── Export / Import ──────────────────────────────────────────────────────────

@router.get("/export")
async def export_all_config():
    """Export all config as a single JSON blob."""
    blob = {}
    for name in [
        "llm_providers.json",
        "fallback_chain.yaml",
        "personality.json",
        "identity_bypass.json",
        "memory_economy_config.json",
        "luna.launch.json",
        "aibrarian_registry.yaml",
    ]:
        path = CONFIG_ROOT / name
        if not path.exists():
            continue
        if name.endswith(".yaml"):
            blob[name] = yaml.safe_load(path.read_text()) or {}
        else:
            blob[name] = json.loads(path.read_text())
    return blob


@router.post("/import")
async def import_config(blob: dict):
    """Import a config blob (from export). Validates before writing."""
    written = []
    for name, data in blob.items():
        if not isinstance(data, dict):
            continue
        path = CONFIG_ROOT / name
        if name.endswith(".yaml"):
            path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        else:
            path.write_text(json.dumps(data, indent=2) + "\n")
        written.append(name)
    return {"success": True, "files_written": written}


# ── Skills Settings ─────────────────────────────────────────────────────────

_VALID_SENSITIVITIES = {"strict", "loose", "aggressive"}
_VALID_DETECTION_MODES = {"regex", "fuzzy", "llm-assisted"}
_SKILL_NAMES = {"math", "logic", "formatting", "reading", "diagnostic", "eden", "analytics", "arcade", "library_research", "memory_recall", "options"}


@router.get("/skills")
async def get_skills_settings():
    """Return full skills configuration."""
    path = CONFIG_ROOT / "skills.yaml"
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
        return raw.get("skills", raw)
    return {"enabled": False, "detection_mode": "regex", "skills": {}}


class SkillsUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_execution_ms: Optional[int] = None
    detection: Optional[dict] = None
    skill_configs: Optional[dict] = None


@router.post("/skills")
async def update_skills_settings(update: SkillsUpdate):
    """Update skills configuration with validation and hot-reload."""
    raw = _read_yaml("skills.yaml")
    skills = raw.get("skills", raw)

    if update.enabled is not None:
        skills["enabled"] = bool(update.enabled)

    if update.max_execution_ms is not None:
        skills["max_execution_ms"] = max(1000, min(30000, update.max_execution_ms))

    if update.detection:
        det = skills.setdefault("detection", {})
        if "mode" in update.detection:
            if update.detection["mode"] not in _VALID_DETECTION_MODES:
                raise HTTPException(status_code=422, detail=f"mode must be one of {_VALID_DETECTION_MODES}")
            det["mode"] = update.detection["mode"]
        if "slash_commands" in update.detection:
            det["slash_commands"] = bool(update.detection["slash_commands"])
        if "llm" in update.detection:
            llm = det.setdefault("llm", {})
            llm_in = update.detection["llm"]
            if "enabled" in llm_in:
                llm["enabled"] = bool(llm_in["enabled"])
            if "model" in llm_in:
                llm["model"] = llm_in["model"]
            if "confidence_threshold" in llm_in:
                llm["confidence_threshold"] = max(0.0, min(1.0, float(llm_in["confidence_threshold"])))
            if "timeout_ms" in llm_in:
                llm["timeout_ms"] = max(100, min(5000, int(llm_in["timeout_ms"])))

    if update.skill_configs:
        for skill_name, conf in update.skill_configs.items():
            if skill_name not in _SKILL_NAMES:
                continue
            target = skills.setdefault(skill_name, {})
            if "enabled" in conf:
                target["enabled"] = bool(conf["enabled"])
            if "sensitivity" in conf:
                if conf["sensitivity"] not in _VALID_SENSITIVITIES:
                    raise HTTPException(status_code=422, detail=f"sensitivity must be one of {_VALID_SENSITIVITIES}")
                target["sensitivity"] = conf["sensitivity"]
            if "slash_command" in conf:
                target["slash_command"] = str(conf["slash_command"])
            if "extra_triggers" in conf:
                if not isinstance(conf["extra_triggers"], list):
                    raise HTTPException(status_code=422, detail="extra_triggers must be a list of strings")
                target["extra_triggers"] = [str(t) for t in conf["extra_triggers"] if t]

    raw["skills"] = skills
    _write_yaml("skills.yaml", raw)

    # Hot-reload: rebuild detector with new config
    try:
        from luna.skills.config import SkillsConfig
        new_config = SkillsConfig.from_yaml(CONFIG_ROOT / "skills.yaml")
        from luna.api.server import _engine
        if _engine:
            director = _engine.get_actor("director")
            if director and director._skill_registry:
                director._skill_registry.reload_config(new_config)
                logger.info("[SETTINGS] Skills config hot-reloaded")
    except Exception as e:
        logger.debug(f"[SETTINGS] Skills hot-reload skipped: {e}")

    return {"success": True}


# ── About / System ──────────────────────────────────────────────────────────

@router.get("/about")
async def get_about():
    """Return engine version and basic stats."""
    import shutil

    data_dir = _PROJECT_ROOT / "data"
    total_size = 0
    if data_dir.exists():
        for f in data_dir.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size

    return {
        "engine_version": "2.0.0",
        "project_root": str(_PROJECT_ROOT),
        "config_root": str(CONFIG_ROOT),
        "data_size_mb": round(total_size / (1024 * 1024), 1),
    }


# ── Display (badge visibility) ──────────────────────────────────────────────

_DISPLAY_DEFAULTS: dict = {
    "badges": {
        "route": True,
        "model": False,
        "tokens": False,
        "latency": True,
        "access_filter": True,
        "lunascript": True,
        "show_knowledge_events": True,
    },
}


@router.get("/display")
async def get_display_settings():
    """Return display / badge-visibility config."""
    path = CONFIG_ROOT / "display.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DISPLAY_DEFAULTS)


@router.post("/display")
async def update_display_settings(request: dict):
    """Merge incoming display config and persist to display.json."""
    path = CONFIG_ROOT / "display.json"
    current = dict(_DISPLAY_DEFAULTS)
    if path.exists():
        try:
            current = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    if "badges" in request:
        current.setdefault("badges", {}).update(request["badges"])

    _write_json("display.json", current)
    return {"success": True}
