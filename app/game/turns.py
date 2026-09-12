"""Turn rotation helpers: who bats next, who bowls next."""
from __future__ import annotations

from app.database.models import GamePlayer


def next_batter(players: list[GamePlayer], current_batter_id: int | None) -> GamePlayer | None:
    """First player (by batting_order) who hasn't batted yet and isn't out."""
    candidates = [p for p in players if not p.is_out and not p.has_batted]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.batting_order)
    return candidates[0]


def next_bowler(players: list[GamePlayer], batter_id: int, current_bowler_id: int | None) -> GamePlayer | None:
    """
    Pick the next bowler: preferring whoever has bowled the fewest overs so
    far (keeps it fair), and never the same bowler as the immediately
    preceding over when alternatives exist.

    In a team match, players are flagged `is_bowler_pool=True` for the fixed
    bowling-side roster - if any such players exist we restrict candidates to
    that roster so a teammate of the batting side is never handed the ball.
    In solo games no player is flagged, so this falls back to "anyone who
    isn't the current batter" as before.
    """
    pool = [p for p in players if p.is_bowler_pool and p.user_id != batter_id]
    candidates = pool if pool else [p for p in players if p.user_id != batter_id]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.overs_bowled)
    if current_bowler_id is not None and len(candidates) > 1:
        alt = [c for c in candidates if c.user_id != current_bowler_id]
        if alt:
            return alt[0]
    return candidates[0]


def all_batters_done(players: list[GamePlayer]) -> bool:
    return all(p.is_out or p.has_batted for p in players)
