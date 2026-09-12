"""
Configurable scoring engine.

Default rule (hand-cricket style):
  - same number from batter and bowler => OUT
  - otherwise, batter scores runs equal to their own chosen number

This is intentionally isolated from the rest of the engine so a different
scoring rule (e.g. runs = difference between numbers) can be swapped in
later without touching turn/over management.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.database.models import BallResult


@dataclass(frozen=True)
class BallOutcome:
    result: BallResult
    runs: int


def resolve_ball(batter_number: int, bowler_number: int) -> BallOutcome:
    if batter_number == bowler_number:
        return BallOutcome(result=BallResult.OUT, runs=0)
    return BallOutcome(result=BallResult.RUNS, runs=batter_number)


def is_boundary_four(runs: int) -> bool:
    return runs == 4


def is_boundary_six(runs: int) -> bool:
    return runs == 6
