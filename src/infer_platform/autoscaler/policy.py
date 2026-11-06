from dataclasses import dataclass, field
from math import ceil

from pydantic import BaseModel, Field, model_validator


class Policy(BaseModel):
    min: int = Field(default=1, ge=1)
    max: int = Field(default=8, ge=1)
    target_backlog_per_replica: float = Field(default=10, gt=0)
    target_busy_ratio: float = Field(default=0.7, gt=0, le=1)
    max_head_age_s: float = Field(default=0.5, gt=0)
    up_cooldown_s: float = Field(default=5, ge=0)
    down_cooldown_s: float = Field(default=30, ge=0)
    stabilization_s: float = Field(default=120, ge=0)
    max_up_step: int = Field(default=4, ge=1)

    @model_validator(mode="after")
    def bounds(self) -> "Policy":
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


@dataclass(frozen=True)
class State:
    current: int
    backlog: int
    mean_busy_ratio: float
    head_age: float
    workers_alive: int = 0


@dataclass(frozen=True)
class History:
    recommendations: tuple[tuple[float, int], ...] = ()
    last_change: float = float("-inf")


@dataclass(frozen=True)
class Decision:
    desired: int
    reason: str
    history: History = field(default_factory=History)


def decide(state: State, policy: Policy, history: History, now: float) -> Decision:
    """Pure recommendation; retain raw demand, not stabilized output, to permit decay."""
    by_backlog = ceil(max(0, state.backlog) / policy.target_backlog_per_replica)
    by_util = ceil(state.current * max(0, state.mean_busy_ratio) / policy.target_busy_ratio)
    by_latency = state.current + 1 if state.head_age > policy.max_head_age_s else 0
    raw = max(policy.min, min(policy.max, max(by_backlog, by_util, by_latency)))
    points = tuple(
        (t, n) for t, n in history.recommendations if now - t < policy.stabilization_s
    ) + ((now, raw),)
    target, reason = raw, "demand"
    if target < state.current:
        target = min(state.current, max(n for _, n in points))
        reason = "stabilization"
    if target > state.current:
        target = min(
            target, max(state.current + 1, state.current * 2), state.current + policy.max_up_step
        )
        if now - history.last_change < policy.up_cooldown_s:
            target, reason = state.current, "up_cooldown"
    elif target < state.current and now - history.last_change < policy.down_cooldown_s:
        target, reason = state.current, "down_cooldown"
    # Bounds override cooldowns and stale recommendations after policy changes.
    target = max(policy.min, min(policy.max, target))
    changed = now if target != state.current else history.last_change
    return Decision(target, reason, History(points, changed))
