"""
Luna Engine Inference Abstraction
=================================

Provides both local (MLX/Qwen) and cloud (Claude) inference.

Usage:
    from luna.inference import LocalInference, HybridInference

    # Local only
    local = LocalInference()
    await local.load_model()
    result = await local.generate("Hello")

    # Hybrid routing
    hybrid = HybridInference()
    response, used_local = await hybrid.route("Quick question")
"""

from .local import (
    LocalInference,
    HybridInference,
    InferenceConfig,
    GenerationResult,
    DEFAULT_MODEL,
    FALLBACK_MODEL,
)

__all__ = [
    "LocalInference",
    "HybridInference",
    "InferenceConfig",
    "GenerationResult",
    "DEFAULT_MODEL",
    "FALLBACK_MODEL",
]
