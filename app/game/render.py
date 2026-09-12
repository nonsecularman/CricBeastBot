"""
Rendering helpers - turns engine state into the premium cricket-themed
message text used in the group status message and scoreboards.
"""
from __future__ import annotations

from app.database.models import Ball, Game, GamePlayer
from app.utils.helpers import SEPARATOR, mention_html, over_ball_string


def solo_queue_text(game: Game, players: list[GamePlayer]) -> str:
    names = "\n".join(f"  {i+1}. {p.display_name}" for i, p in enumerate(players)) or "  (empty)"
    return (
        f"{SEPARATOR}\n"
        f"⚖️ <b>Spell:</b> {game.balls_per_over} balls per turn\n\n"
        "├ /join — enter the queue\n"
        "├ /leavesolo — exit the queue\n"
        "└ /startsolo — force-start\n\n"
        f"👥 <b>Players ({len(players)}):</b>\n{names}\n\n"
        "⏳ Auto-starts once enough players join.\n"
        f"{SEPARATOR}"
    )


def _current_over_balls(balls: list[Ball], over_number: int) -> list[int | str]:
    out = []
    for b in balls:
        if b.over_number != over_number:
            continue
        out.append("W" if b.result.value == "out" else b.runs)
    return out


def status_text(game: Game, batter: GamePlayer, bowler: GamePlayer, balls: list[Ball], waiting_on_dm: bool) -> str:
    header = (
        "📊 <b>Status:</b>\n"
        f"🏏 Batter: {mention_html(batter.user_id, batter.display_name)} ({batter.runs} off {batter.balls_faced})\n"
        f"🥎 Bowler: {mention_html(bowler.user_id, bowler.display_name)} "
        f"(Over: {game.balls_this_over}/{game.balls_per_over} balls)"
    )
    this_over = _current_over_balls(balls, game.current_over)
    over_line = f"\nOver {game.current_over}: {over_ball_string(this_over)}"
    footer = (
        f"\n\n👉 {mention_html(bowler.user_id, bowler.display_name)}, check your DM to bowl! 🤫🥎"
        if waiting_on_dm
        else ""
    )
    return header + over_line + footer


def wicket_text(batter: GamePlayer) -> str:
    return (
        "💥 <b>WICKET!</b>\n\n"
        f"🏏 {mention_html(batter.user_id, batter.display_name)} is OUT!\n\n"
        f"Score:\n{batter.runs} runs\n\n"
        f"Balls:\n{batter.balls_faced}"
    )


def ball_result_text(
    batter: GamePlayer,
    bowler: GamePlayer,
    batter_number: int,
    bowler_number: int,
    runs: int,
    is_wicket: bool,
) -> str:
    """
    The per-ball 'both numbers revealed' message that goes out with the GIF
    right after a ball is resolved - tags both players, exactly like the
    reference gameplay: bowler delivery + batter hit, then the outcome line.
    """
    header = (
        "🎾 <b>Ball delivered!</b> 🎾\n\n"
        f"🥎 Bowler delivery: <b>{bowler_number}</b>\n"
        f"🏏 Batter hit: <b>{batter_number}</b>\n\n"
    )
    batter_tag = mention_html(batter.user_id, batter.display_name)
    bowler_tag = mention_html(bowler.user_id, bowler.display_name)
    if is_wicket:
        outcome = (
            "💥 <b>HOWZAT! OUT!</b> ❌❌\n"
            f"{batter_tag} is dismissed for {batter.runs}!\n"
            f"🥎 Well bowled, {bowler_tag}!"
        )
    elif runs == 6:
        outcome = f"🚀 <b>SIX!</b> {batter_tag} smashes it out of the park!"
    elif runs == 4:
        outcome = f"🔥 <b>FOUR!</b> {batter_tag} finds the boundary!"
    else:
        outcome = f"🏏 {batter_tag} takes <b>{runs}</b> run{'s' if runs != 1 else ''} off {bowler_tag}."
    return header + outcome


def scoreboard_text(game: Game, batter: GamePlayer, bowler: GamePlayer, over_balls: list[int | str]) -> str:
    status = "NOT OUT" if not batter.is_out else "OUT"
    return (
        "╭──────────────╮\n"
        "🏏 <b>SCOREBOARD</b>\n"
        "╰──────────────╯\n\n"
        f"🏏 {batter.display_name}\n"
        f"Runs: {batter.runs}\n"
        f"Balls: {batter.balls_faced}\n"
        f"Status: {status}\n\n"
        f"🥎 {bowler.display_name}\n"
        f"Overs: {bowler.overs_bowled}\n"
        f"Runs Conceded: {bowler.runs_conceded}\n\n"
        f"Over:\n{over_ball_string(over_balls)}"
    )


def final_result_text(game: Game, players: list[GamePlayer], winner: GamePlayer | None) -> str:
    ranked = sorted(players, key=lambda p: p.runs, reverse=True)
    lines = [
        f"{i+1}. {p.display_name} — {p.runs} runs ({p.balls_faced} balls){' 🏆' if winner and p.user_id == winner.user_id else ''}"
        for i, p in enumerate(ranked)
    ]
    winner_line = f"\n🏆 <b>Winner: {winner.display_name}!</b>" if winner else ""
    return (
        "🏁 <b>GAME OVER</b>\n\n" + "\n".join(lines) + winner_line
    )


def bowler_dm_text(game: Game, batter: GamePlayer, bowler_score_line: str) -> str:
    return (
        "🥎 <b>BOWLING TURN</b>\n\n"
        f"🏏 Batter: {batter.display_name}\n"
        f"📊 Score: {bowler_score_line}\n\n"
        "Choose your number:"
    )


def batter_turn_text(batter: GamePlayer) -> str:
    """
    Sent as a brand-new group message (never an edit) so the batter actually
    gets a Telegram notification/ping for their turn - mirroring the fresh
    DM the bowler gets each ball. Edited messages don't trigger pings even
    if a mention is inside them, which is why this has to be its own send.
    """
    return f"👉 {mention_html(batter.user_id, batter.display_name)}, it's your turn to bat! 🏏 Pick a number below."


def bowl_locked_text() -> str:
    return "✅ Your bowl has been locked.\n\nWait for the batter..."
