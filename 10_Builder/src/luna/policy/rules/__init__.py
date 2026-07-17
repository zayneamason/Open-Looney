"""Rule modules for Memory Policy v1.0."""

from luna.policy.rules.rule_1_admission import AdmitDecision, Rule_1_Admission
from luna.policy.rules.rule_2_decay import DecayResult, Rule_2_Decay
from luna.policy.rules.rule_3_transitions import Movement, Rule_3_Transitions
from luna.policy.rules.rule_4_eviction import Eviction, Rule_4_Eviction
from luna.policy.rules.rule_5_dedup import DedupDecision, Rule_5_Dedup

__all__ = [
    "AdmitDecision",
    "DecayResult",
    "DedupDecision",
    "Eviction",
    "Movement",
    "Rule_1_Admission",
    "Rule_2_Decay",
    "Rule_3_Transitions",
    "Rule_4_Eviction",
    "Rule_5_Dedup",
]
