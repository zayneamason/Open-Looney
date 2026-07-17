"""Shared GPU lock — serializes Metal access across MLX inference and embedding models.

Prevents the AGXCommandBuffer assertion crash when MLX (Qwen) and
sentence-transformers (MiniLM) both try to use the Metal GPU encoder
simultaneously on Apple Silicon.

Usage:
    from luna.core.gpu_lock import gpu_lock

    with gpu_lock:
        # Metal-touching operation here
        model.generate(...)
"""

import threading

gpu_lock = threading.Lock()
