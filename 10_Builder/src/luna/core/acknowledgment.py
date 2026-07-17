"""
Acknowledgment Router
=====================

Smart acknowledgment generation with tiered routing:
- FAST PATH (~2ms): Keyword matching for simple queries
- SEMANTIC PATH (~30-50ms): MiniLM embeddings for complex queries

The router decides which path based on query length and complexity signals.
"""

from __future__ import annotations

import logging
import time
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Tuple
import numpy as np

logger = logging.getLogger(__name__)

# Lazy import for embeddings (avoid startup cost if not needed)
_embeddings_instance = None


class AckRoute(Enum):
    """Which path was used to generate acknowledgment."""
    FAST = "fast"
    SEMANTIC = "semantic"


@dataclass
class AckResult:
    """Result from acknowledgment generation."""
    text: str
    route: AckRoute
    intent: str
    sentiment: str
    latency_ms: float


# =============================================================================
# FAST PATH: Keyword matching (~2ms)
# =============================================================================

# Intent patterns: (intent_name, keywords, acknowledgments)
# NOTE: Greetings intentionally excluded - responding to "hey" with "hey~" felt redundant
INTENT_PATTERNS = [
    # Memory queries
    ("memory",
     ["remember", "recall", "memory", "memories", "forgot", "forget"],
     ["hmm let me think~", "oh that...", "let me recall~"]),

    # Emotional/relational
    ("emotional",
     ["feel about", "how do you feel", "love", "care about", "miss", "think of me"],
     ["oh~", "aww~", "that's sweet~"]),

    # How are you
    ("wellbeing",
     ["how are you", "how're you", "how you doing", "what's up with you"],
     ["doing good~", "I'm here~", "feeling present~"]),

    # Help/task
    ("help",
     ["help", "can you", "could you", "would you", "please"],
     ["sure~", "on it~", "let's see~"]),

    # Questions
    ("question",
     ["what", "why", "how", "when", "where", "who", "which"],
     ["hmm~", "good question~", "let's see~"]),

    # Frustration
    ("frustration",
     ["frustrated", "annoying", "annoyed", "ugh", "argh", "damn", "stuck"],
     ["oh no~", "ugh I feel that~", "let's figure this out~"]),

    # Excitement
    ("excitement",
     ["excited", "amazing", "awesome", "great news", "guess what"],
     ["ooh~", "tell me~", "what's up~"]),

    # Continuation
    ("continuation",
     ["anyway", "so", "as I was", "back to", "where were we"],
     ["right~", "okay so~", "where were we~"]),
]

# Sentiment keywords
POSITIVE_WORDS = {"happy", "glad", "excited", "love", "great", "awesome", "amazing", "good", "wonderful", "fantastic"}
NEGATIVE_WORDS = {"sad", "frustrated", "angry", "annoyed", "upset", "confused", "stuck", "lost", "worried", "stressed"}

# Default acknowledgment
DEFAULT_ACK = "one sec~"


def _detect_sentiment(query: str) -> str:
    """Quick sentiment detection via keyword matching."""
    words = set(query.lower().split())

    pos_count = len(words & POSITIVE_WORDS)
    neg_count = len(words & NEGATIVE_WORDS)

    if neg_count > pos_count:
        return "negative"
    elif pos_count > neg_count:
        return "positive"
    return "neutral"


def _fast_path(query: str) -> AckResult:
    """
    Fast acknowledgment via keyword matching.
    ~1-2ms latency.
    """
    start = time.perf_counter()
    query_lower = query.lower()

    # Check each intent pattern
    for intent_name, keywords, acks in INTENT_PATTERNS:
        for keyword in keywords:
            if keyword in query_lower:
                # Found a match - pick acknowledgment based on sentiment
                sentiment = _detect_sentiment(query)

                # Pick appropriate ack (could be smarter here)
                import random
                ack = random.choice(acks)

                latency = (time.perf_counter() - start) * 1000
                logger.debug(f"[ACK-FAST] intent={intent_name}, sentiment={sentiment}, latency={latency:.2f}ms")

                return AckResult(
                    text=ack,
                    route=AckRoute.FAST,
                    intent=intent_name,
                    sentiment=sentiment,
                    latency_ms=latency
                )

    # No match - use default
    sentiment = _detect_sentiment(query)
    latency = (time.perf_counter() - start) * 1000

    return AckResult(
        text=DEFAULT_ACK,
        route=AckRoute.FAST,
        intent="unknown",
        sentiment=sentiment,
        latency_ms=latency
    )


# =============================================================================
# SEMANTIC PATH: MiniLM embeddings (~30-50ms)
# =============================================================================

# Intent clusters with exemplar phrases (pre-computed at startup)
# NOTE: Greetings excluded - Luna just responds directly without acknowledgment
INTENT_CLUSTERS = {
    "memory": [
        "do you remember when we",
        "what about that time",
        "can you recall",
        "think back to",
        "our conversation about",
    ],
    "emotional": [
        "how do you feel about me",
        "what do you think of our relationship",
        "do you care about me",
        "are we close",
        "I value our connection",
    ],
    "frustration": [
        "this is really frustrating",
        "I'm stuck and don't know what to do",
        "nothing is working",
        "I'm so annoyed right now",
        "this is driving me crazy",
    ],
    "deep_thought": [
        "I've been thinking about what you said",
        "there's something I need to process",
        "I'm not sure how I feel about this",
        "can we talk about something important",
        "I need to work through something",
    ],
    "curiosity": [
        "tell me something interesting",
        "what do you think about",
        "I'm curious about",
        "explain this to me",
        "help me understand",
    ],
    "help": [
        "can you help me with",
        "I need assistance",
        "could you do something for me",
        "I'm trying to figure out",
        "would you mind helping",
    ],
    "excitement": [
        "guess what happened",
        "I have great news",
        "something amazing just occurred",
        "you won't believe this",
        "I'm so excited to tell you",
    ],
}

# Acknowledgments for semantic intents (greeting excluded)
SEMANTIC_ACKS = {
    "memory": ["hmm let me think back~", "oh that...", "let me remember~"],
    "emotional": ["oh~ that means a lot", "aww~", "that's sweet to hear~"],
    "frustration": ["oh no~ I'm here", "ugh that sounds rough~", "let's work through this~"],
    "deep_thought": ["I hear you~", "take your time~", "I'm listening~"],
    "curiosity": ["ooh good question~", "let's explore that~", "hmm~"],
    "help": ["of course~", "I got you~", "let's do this~"],
    "excitement": ["ooh tell me~", "what happened~", "I wanna hear~"],
}

# Pre-computed cluster embeddings (initialized lazily)
_cluster_embeddings: Optional[dict] = None


def _get_embeddings():
    """Get the singleton LocalEmbeddings instance."""
    global _embeddings_instance
    if _embeddings_instance is None:
        try:
            from luna.substrate.local_embeddings import get_embeddings
            _embeddings_instance = get_embeddings()
        except ImportError:
            logger.warning("[ACK-SEMANTIC] LocalEmbeddings not available")
            return None
    return _embeddings_instance


def _ensure_cluster_embeddings() -> bool:
    """Pre-compute cluster embeddings if not already done."""
    global _cluster_embeddings

    if _cluster_embeddings is not None:
        return True

    embeddings = _get_embeddings()
    if embeddings is None:
        return False

    logger.info("[ACK-SEMANTIC] Pre-computing intent cluster embeddings...")
    start = time.perf_counter()

    _cluster_embeddings = {}
    for intent, exemplars in INTENT_CLUSTERS.items():
        # Encode all exemplars and compute centroid
        vectors = embeddings.encode_batch(exemplars)
        if vectors is not None and len(vectors) > 0:
            centroid = np.mean(vectors, axis=0)
            _cluster_embeddings[intent] = centroid

    elapsed = (time.perf_counter() - start) * 1000
    logger.info(f"[ACK-SEMANTIC] Pre-computed {len(_cluster_embeddings)} clusters in {elapsed:.0f}ms")

    return len(_cluster_embeddings) > 0


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def _semantic_path(query: str) -> AckResult:
    """
    Semantic acknowledgment via MiniLM embeddings.
    ~30-50ms latency.
    """
    start = time.perf_counter()

    # Ensure clusters are pre-computed
    if not _ensure_cluster_embeddings():
        # Fall back to fast path
        logger.warning("[ACK-SEMANTIC] Clusters not available, falling back to fast path")
        return _fast_path(query)

    embeddings = _get_embeddings()
    if embeddings is None:
        return _fast_path(query)

    # Encode query
    query_vec = embeddings.encode(query)
    if query_vec is None:
        return _fast_path(query)

    # Find best matching cluster
    best_intent = "unknown"
    best_score = -1.0

    for intent, centroid in _cluster_embeddings.items():
        score = _cosine_similarity(query_vec, centroid)
        if score > best_score:
            best_score = score
            best_intent = intent

    # Get sentiment
    sentiment = _detect_sentiment(query)

    # Adjust based on confidence
    if best_score < 0.3:
        # Low confidence - use default
        ack = DEFAULT_ACK
        best_intent = "uncertain"
    else:
        # Pick acknowledgment for this intent
        import random
        acks = SEMANTIC_ACKS.get(best_intent, [DEFAULT_ACK])
        ack = random.choice(acks)

    latency = (time.perf_counter() - start) * 1000
    logger.debug(f"[ACK-SEMANTIC] intent={best_intent} (score={best_score:.2f}), sentiment={sentiment}, latency={latency:.2f}ms")

    return AckResult(
        text=ack,
        route=AckRoute.SEMANTIC,
        intent=best_intent,
        sentiment=sentiment,
        latency_ms=latency
    )


# =============================================================================
# ROUTER: Decides which path to use
# =============================================================================

# Router thresholds
SHORT_QUERY_THRESHOLD = 30  # chars
COMPLEXITY_SIGNALS = [
    r"\bI've been\b",
    r"\bI'm not sure\b",
    r"\bcan we talk\b",
    r"\bthinking about\b",
    r"\bfeel like\b",
    r"\bwhat you said\b",
    r"\blast time\b",
]


def _should_use_semantic(query: str) -> bool:
    """Decide if query needs semantic understanding."""
    # Short queries -> fast path
    if len(query) < SHORT_QUERY_THRESHOLD:
        return False

    # Check for complexity signals
    for pattern in COMPLEXITY_SIGNALS:
        if re.search(pattern, query, re.IGNORECASE):
            return True

    # Long queries without clear keyword matches -> semantic
    query_lower = query.lower()
    for intent_name, keywords, _ in INTENT_PATTERNS:
        for keyword in keywords:
            if keyword in query_lower:
                return False  # Has clear keyword, use fast path

    # No clear match and long -> semantic
    return len(query) > 50


def generate_acknowledgment(query: str) -> AckResult:
    """
    Generate a contextual acknowledgment for the user's query.

    Routes to fast path (~2ms) or semantic path (~30-50ms) based on
    query complexity.

    Args:
        query: The user's message

    Returns:
        AckResult with acknowledgment text and metadata
    """
    if _should_use_semantic(query):
        return _semantic_path(query)
    else:
        return _fast_path(query)


def precompute_clusters():
    """
    Pre-compute intent cluster embeddings.

    Call this at startup to avoid latency on first semantic query.
    """
    _ensure_cluster_embeddings()


# =============================================================================
# MODULE INIT
# =============================================================================

__all__ = [
    "generate_acknowledgment",
    "precompute_clusters",
    "AckResult",
    "AckRoute",
]
