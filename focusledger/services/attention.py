from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focusledger.config import AppConfig


ACTIVE = "active"
LOW_ACTIVITY = "low_activity"
ATTENTION_GAP = "attention_gap"
INTERRUPTED = "interrupted"
HARD_IDLE = "hard_idle"
UNKNOWN = "unknown"

ATTENTION_STATES = (
    ACTIVE,
    LOW_ACTIVITY,
    ATTENTION_GAP,
    INTERRUPTED,
    HARD_IDLE,
)


@dataclass(frozen=True)
class AttentionResult:
    state: str
    weight: float


@dataclass(frozen=True)
class AttentionPolicy:
    low_activity_after_seconds: int = 180
    attention_gap_after_seconds: int = 600
    interrupted_after_seconds: int = 1200
    hard_idle_after_seconds: int = 1800
    active_weight: float = 1.0
    low_activity_weight: float = 0.5
    attention_gap_weight: float = 0.0
    interrupted_weight: float = 0.0
    hard_idle_weight: float = 0.0

    @classmethod
    def from_config(cls, config: AppConfig) -> AttentionPolicy:
        return cls(
            low_activity_after_seconds=config.low_activity_after_seconds,
            attention_gap_after_seconds=config.attention_gap_after_seconds,
            interrupted_after_seconds=config.interrupted_after_seconds,
            hard_idle_after_seconds=config.hard_idle_after_seconds,
            active_weight=config.attention_active_weight,
            low_activity_weight=config.attention_low_activity_weight,
            attention_gap_weight=config.attention_gap_weight,
            interrupted_weight=config.attention_interrupted_weight,
            hard_idle_weight=config.attention_hard_idle_weight,
        )

    def validate(self) -> None:
        thresholds = (
            self.low_activity_after_seconds,
            self.attention_gap_after_seconds,
            self.interrupted_after_seconds,
            self.hard_idle_after_seconds,
        )
        if thresholds[0] < 0 or any(
            current >= following
            for current, following in zip(thresholds, thresholds[1:])
        ):
            raise ValueError(
                "attention thresholds must be non-negative and strictly increasing"
            )
        weights = (
            self.active_weight,
            self.low_activity_weight,
            self.attention_gap_weight,
            self.interrupted_weight,
            self.hard_idle_weight,
        )
        if any(weight < 0.0 or weight > 1.0 for weight in weights):
            raise ValueError("attention weights must be between 0.0 and 1.0")


def classify_attention(
    idle_seconds: float | None,
    policy: AttentionPolicy,
) -> AttentionResult:
    policy.validate()
    if idle_seconds is None:
        return AttentionResult(UNKNOWN, 0.0)

    idle = max(0.0, float(idle_seconds))
    if idle < policy.low_activity_after_seconds:
        return AttentionResult(ACTIVE, policy.active_weight)
    if idle < policy.attention_gap_after_seconds:
        return AttentionResult(LOW_ACTIVITY, policy.low_activity_weight)
    if idle < policy.interrupted_after_seconds:
        return AttentionResult(ATTENTION_GAP, policy.attention_gap_weight)
    if idle < policy.hard_idle_after_seconds:
        return AttentionResult(INTERRUPTED, policy.interrupted_weight)
    return AttentionResult(HARD_IDLE, policy.hard_idle_weight)
