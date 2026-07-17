"""LunaScript calibration baselines — corpus statistics for z-scoring features.

Loads from DB, calibrates from corpus, or falls back to hardcoded values
from the original 1268-response calibration run.
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional

from .features import ALL_FEATURES, _split_sentences

logger = logging.getLogger(__name__)


@dataclass
class BaselineStats:
    mean: float
    stddev: float
    min_val: float
    max_val: float
    p25: float
    p50: float
    p75: float
    n: int


def get_hardcoded_baselines() -> dict[str, BaselineStats]:
    """Fallback baselines from the original 1268-response calibration."""
    return {
        "avg_sentence_length":       BaselineStats(12.7805, 5.0167, 0.0, 52.0, 0.0, 12.3636, 52.0, 1268),
        "avg_word_length":           BaselineStats(4.7200, 0.4366, 3.0417, 6.2301, 0.0, 4.7393, 6.2301, 1268),
        "closing_question":          BaselineStats(0.6901, 0.4626, 0.0, 1.0, 0.0, 1.0, 1.0, 1268),
        "conditional_ratio":         BaselineStats(0.0070, 0.0103, 0.0, 0.0625, 0.0, 0.0, 0.0625, 1268),
        "contraction_rate":          BaselineStats(0.9162, 0.1469, 0.0, 1.0, 0.0, 1.0, 1.0, 1268),
        "emoji_density":             BaselineStats(0.0076, 0.0135, 0.0, 0.1176, 0.0, 0.0, 0.1176, 1268),
        "emphasis_density":          BaselineStats(0.0312, 0.0228, 0.0, 0.2000, 0.0, 0.0284, 0.2, 1268),
        "exploratory_ratio":         BaselineStats(0.0118, 0.0111, 0.0, 0.1437, 0.0, 0.0102, 0.1437, 1268),
        "filler_ratio":              BaselineStats(0.0171, 0.0168, 0.0, 0.1176, 0.0, 0.0133, 0.1176, 1268),
        "first_person_ratio":        BaselineStats(0.0497, 0.0319, 0.0, 0.1765, 0.0, 0.0490, 0.1765, 1268),
        "formal_vocab_ratio":        BaselineStats(0.0004, 0.0019, 0.0, 0.0294, 0.0, 0.0, 0.0294, 1268),
        "hedge_ratio":               BaselineStats(0.0020, 0.0055, 0.0, 0.0500, 0.0, 0.0, 0.0500, 1268),
        "list_usage":                BaselineStats(0.0534, 0.2177, 0.0, 3.5000, 0.0, 0.0, 3.5, 1268),
        "opening_reaction":          BaselineStats(0.3981, 0.4103, 0.0, 1.0, 0.0, 0.0, 1.0, 1268),
        "passive_ratio":             BaselineStats(0.0147, 0.0537, 0.0, 0.5000, 0.0, 0.0, 0.5, 1268),
        "question_density":          BaselineStats(0.2553, 0.1988, 0.0, 1.0, 0.0, 0.2500, 1.0, 1268),
        "sentence_length_variance":  BaselineStats(94.3188, 157.2532, 0.0, 2289.5833, 0.0, 52.6222, 2289.5833, 1268),
        "slang_ratio":               BaselineStats(0.0044, 0.0088, 0.0, 0.0667, 0.0, 0.0, 0.0667, 1268),
        "tangent_markers":           BaselineStats(0.0111, 0.0107, 0.0, 0.0761, 0.0, 0.0093, 0.0761, 1268),
        "we_ratio":                  BaselineStats(0.0117, 0.0157, 0.0, 0.1290, 0.0, 0.0064, 0.1290, 1268),
        "you_ratio":                 BaselineStats(0.0358, 0.0295, 0.0, 0.1667, 0.0, 0.0286, 0.1667, 1268),
    }


async def load_baselines(db) -> Optional[dict[str, BaselineStats]]:
    """Load baselines from lunascript_baselines table."""
    try:
        rows = await db.fetchall(
            "SELECT feature_name, mean, stddev, min_val, max_val, p25, p50, p75, n "
            "FROM lunascript_baselines"
        )
        if not rows:
            return None
        baselines = {}
        for row in rows:
            baselines[row[0]] = BaselineStats(
                mean=row[1], stddev=row[2], min_val=row[3], max_val=row[4],
                p25=row[5], p50=row[6], p75=row[7], n=row[8],
            )
        return baselines
    except Exception as e:
        logger.warning(f"[LUNASCRIPT] Failed to load baselines: {e}")
        return None


async def save_baselines(db, baselines: dict[str, BaselineStats]) -> None:
    """Upsert baselines to lunascript_baselines table."""
    now = time.time()
    for feat_name, bl in baselines.items():
        await db.execute(
            "INSERT OR REPLACE INTO lunascript_baselines "
            "(feature_name, mean, stddev, min_val, max_val, p25, p50, p75, n, calibrated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (feat_name, bl.mean, bl.stddev, bl.min_val, bl.max_val,
             bl.p25, bl.p50, bl.p75, bl.n, now),
        )


async def calibrate_from_corpus(db, min_corpus_size: int = 50) -> Optional[dict[str, BaselineStats]]:
    """Calibrate baselines from assistant turns in conversation_turns table."""
    import re as _re

    try:
        rows = await db.fetchall(
            "SELECT content FROM conversation_turns "
            "WHERE role = 'assistant' AND length(content) > 80"
        )
    except Exception as e:
        logger.warning(f"[LUNASCRIPT] Cannot read conversation_turns: {e}")
        return None

    if not rows or len(rows) < min_corpus_size:
        logger.info(f"[LUNASCRIPT] Corpus too small ({len(rows) if rows else 0} < {min_corpus_size}), using hardcoded baselines")
        return None

    logger.info(f"[LUNASCRIPT] Calibrating from {len(rows)} assistant responses")

    # Extract features from every response
    feature_values: dict[str, list[float]] = {name: [] for name in ALL_FEATURES}
    for row in rows:
        text = row[0]
        words = _re.findall(r"\b\w+(?:['']\w+)?\b", text)
        for feat_name, func in ALL_FEATURES.items():
            feature_values[feat_name].append(func(text, words))

    # Compute statistics
    baselines = {}
    for feat_name, values in feature_values.items():
        values.sort()
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        stddev = variance ** 0.5

        baselines[feat_name] = BaselineStats(
            mean=mean,
            stddev=stddev,
            min_val=values[0],
            max_val=values[-1],
            p25=values[int(n * 0.25)],
            p50=values[int(n * 0.50)],
            p75=values[int(n * 0.75)],
            n=n,
        )

    return baselines
