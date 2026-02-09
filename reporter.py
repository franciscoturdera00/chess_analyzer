import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

import anthropic
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

import config


def _collect_all_mistakes(analysis_results):
    """Flatten all mistakes from all games into a single list with game context."""
    all_mistakes = []
    for game in analysis_results:
        for mistake in game.get("mistakes", []):
            entry = dict(mistake)
            entry["game_url"] = game.get("game_url", "")
            entry["opponent"] = game.get("opponent", "Unknown")
            entry["player_rating"] = game.get("player_rating", 0)
            entry["result"] = game.get("result", "")
            entry["time_class"] = game.get("time_class", "")
            entry["opening_name"] = game.get("opening_name", "Unknown")
            entry["player_color"] = game.get("player_color", "")
            all_mistakes.append(entry)
    return all_mistakes


def _summary_stats(analysis_results, all_mistakes):
    """Compute summary statistics."""
    total_games = len(analysis_results)
    total_moves = sum(g.get("total_moves", 0) for g in analysis_results)
    total_mistakes = len(all_mistakes)

    inaccuracies = sum(1 for m in all_mistakes if m.get("severity") == "inaccuracy")
    mistakes = sum(1 for m in all_mistakes if m.get("severity") == "mistake")
    blunders = sum(1 for m in all_mistakes if m.get("severity") == "blunder")

    avg_cp_per_game = (
        sum(g.get("avg_cp_loss", 0) for g in analysis_results) / total_games
        if total_games > 0 else 0
    )

    # Date range
    end_times = [g.get("end_time", 0) for g in analysis_results if g.get("end_time")]
    if end_times:
        earliest = datetime.fromtimestamp(min(end_times), tz=timezone.utc).strftime("%Y-%m-%d")
        latest = datetime.fromtimestamp(max(end_times), tz=timezone.utc).strftime("%Y-%m-%d")
        date_range = f"{earliest} to {latest}"
    else:
        date_range = "N/A"

    return {
        "total_games": total_games,
        "total_moves": total_moves,
        "total_mistakes": total_mistakes,
        "inaccuracies": inaccuracies,
        "mistakes": mistakes,
        "blunders": blunders,
        "avg_cp_loss_per_game": round(avg_cp_per_game, 1),
        "date_range": date_range,
    }


def _theme_analysis(all_mistakes):
    """Analyze tactical themes from classified mistakes."""
    theme_counts = Counter()
    theme_cp_loss = defaultdict(list)
    theme_examples = {}

    for m in all_mistakes:
        cls = m.get("classification", {})
        theme = cls.get("primary_theme")
        if not theme:
            continue

        theme_counts[theme] += 1
        theme_cp_loss[theme].append(m.get("eval_swing", 0))

        if theme not in theme_examples:
            theme_examples[theme] = {
                "opponent": m.get("opponent", "?"),
                "move_number": m.get("move_number", "?"),
                "game_url": m.get("game_url", ""),
            }

    results = []
    for theme, count in theme_counts.most_common():
        avg_loss = sum(theme_cp_loss[theme]) / len(theme_cp_loss[theme])
        example = theme_examples.get(theme, {})
        results.append({
            "theme": theme.replace("_", " ").title(),
            "theme_key": theme,
            "count": count,
            "avg_cp_loss": round(avg_loss),
            "example_opponent": example.get("opponent", "?"),
            "example_move": example.get("move_number", "?"),
        })

    return results


def _phase_analysis(all_mistakes, analysis_results):
    """Analyze mistakes by game phase."""
    phase_mistakes = defaultdict(list)
    phase_moves = defaultdict(int)

    # Count total player moves per phase (approximate from mistakes' game context)
    for m in all_mistakes:
        phase = m.get("game_phase", "unknown")
        phase_mistakes[phase].append(m)

    # Count total moves per phase from all games
    # We approximate: opening ~10 moves, then rest split by phase
    for game in analysis_results:
        total = game.get("total_moves", 0)
        game_mistakes = game.get("mistakes", [])
        opening_mistakes = sum(1 for m in game_mistakes if m.get("game_phase") == "opening")
        middle_mistakes = sum(1 for m in game_mistakes if m.get("game_phase") == "middlegame")
        endgame_mistakes = sum(1 for m in game_mistakes if m.get("game_phase") == "endgame")

        # Rough split of moves per phase
        phase_moves["opening"] += min(10, total)
        remaining = max(0, total - 10)
        if remaining > 0:
            phase_moves["middlegame"] += remaining // 2
            phase_moves["endgame"] += remaining - remaining // 2

    results = {}
    for phase in ["opening", "middlegame", "endgame"]:
        mistakes = phase_mistakes.get(phase, [])
        moves = phase_moves.get(phase, 1)  # Avoid division by zero
        mistake_rate = len(mistakes) / moves * 100 if moves > 0 else 0

        # Most common theme in this phase
        themes = Counter()
        for m in mistakes:
            cls = m.get("classification", {})
            t = cls.get("primary_theme")
            if t:
                themes[t] += 1
        top_theme = themes.most_common(1)[0][0].replace("_", " ").title() if themes else "N/A"

        results[phase] = {
            "total_mistakes": len(mistakes),
            "mistake_rate": round(mistake_rate, 1),
            "top_theme": top_theme,
        }

    return results


def _time_pressure_analysis(all_mistakes):
    """Analyze mistakes by remaining clock time."""
    buckets = {
        "plenty (>3 min)": [],
        "moderate (1-3 min)": [],
        "time pressure (<1 min)": [],
        "unknown": [],
    }

    for m in all_mistakes:
        clock = m.get("clock_seconds")
        if clock is None:
            buckets["unknown"].append(m)
        elif clock > 180:
            buckets["plenty (>3 min)"].append(m)
        elif clock > 60:
            buckets["moderate (1-3 min)"].append(m)
        else:
            buckets["time pressure (<1 min)"].append(m)

    results = {}
    for label, mistakes in buckets.items():
        if label == "unknown":
            continue
        avg_swing = (
            sum(m.get("eval_swing", 0) for m in mistakes) / len(mistakes)
            if mistakes else 0
        )
        themes = Counter()
        for m in mistakes:
            cls = m.get("classification", {})
            t = cls.get("primary_theme")
            if t:
                themes[t] += 1
        top_theme = themes.most_common(1)[0][0].replace("_", " ").title() if themes else "N/A"

        results[label] = {
            "mistake_count": len(mistakes),
            "avg_cp_loss": round(avg_swing),
            "top_theme": top_theme,
        }

    return results


def _opening_analysis(analysis_results):
    """Analyze performance by opening."""
    opening_stats = defaultdict(lambda: {
        "games": 0, "wins": 0, "losses": 0, "draws": 0,
        "total_cp_loss": 0, "mistakes": 0,
    })

    for game in analysis_results:
        opening = game.get("opening_name", "Unknown")
        stats = opening_stats[opening]
        stats["games"] += 1
        stats["total_cp_loss"] += game.get("avg_cp_loss", 0)
        stats["mistakes"] += len(game.get("mistakes", []))

        result = game.get("result", "")
        if result == "win":
            stats["wins"] += 1
        elif result in ("checkmated", "timeout", "resigned", "lose", "abandoned"):
            stats["losses"] += 1
        else:
            stats["draws"] += 1

    results = []
    for opening, stats in sorted(opening_stats.items(), key=lambda x: x[1]["games"], reverse=True):
        avg_cp = stats["total_cp_loss"] / stats["games"] if stats["games"] > 0 else 0
        results.append({
            "opening": opening,
            "games": stats["games"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "draws": stats["draws"],
            "avg_cp_loss": round(avg_cp, 1),
            "mistakes_per_game": round(stats["mistakes"] / stats["games"], 1) if stats["games"] > 0 else 0,
        })

    return results


def _ai_pattern_analysis(analysis_results, all_mistakes):
    """Send aggregated data to Claude Opus for high-level pattern analysis."""
    # Build the dataset for Claude
    mistake_data = []
    for m in all_mistakes:
        cls = m.get("classification", {})
        mistake_data.append({
            "theme": cls.get("primary_theme", "unclassified"),
            "secondary_theme": cls.get("secondary_theme"),
            "difficulty": cls.get("difficulty", "unknown"),
            "phase": m.get("game_phase", "unknown"),
            "clock_seconds": m.get("clock_seconds"),
            "opening": m.get("opening_name", "Unknown"),
            "eval_swing": m.get("eval_swing", 0),
            "move_number": m.get("move_number", 0),
            "severity": m.get("severity", "unknown"),
            "opponent": m.get("opponent", "Unknown"),
            "result": m.get("result", ""),
            "player_rating": m.get("player_rating", 0),
        })

    n_games = len(analysis_results)
    prompt = f"""Here are all the mistakes from my last {n_games} chess games, classified by tactical theme:

{json.dumps(mistake_data, indent=2)}

Analyze this data and identify:
1. My biggest recurring blind spots (patterns, not just counts)
2. Correlations I might not notice (e.g., do I miss tactics more in certain openings? After certain move patterns? When I'm ahead/behind in material?)
3. Specific training recommendations ranked by impact
4. Any psychological patterns (e.g., time pressure mistakes, tilting after blunders, playing too safe when ahead)
5. A priority-ordered improvement plan: what should I work on first, second, third?

Be specific and cite actual examples from the data. Don't just say "work on forks" — say "you missed 8 knight forks, 6 of which were in rook+knight endgames when you had less than 90 seconds, suggesting you stop calculating knight moves under time pressure."
"""

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config.ANALYSIS_MODEL,
        max_tokens=config.ANALYSIS_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text


def _render_terminal(report_data, verbose=False):
    """Render report to terminal using rich."""
    console = Console()

    # Title
    console.print()
    console.print(Panel("[bold]Chess Weakness Analyzer Report[/bold]", expand=False))
    console.print()

    # Summary
    stats = report_data["summary"]
    summary_table = Table(title="Summary Statistics", show_header=False)
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold")
    summary_table.add_row("Total Games", str(stats["total_games"]))
    summary_table.add_row("Total Moves Analyzed", str(stats["total_moves"]))
    summary_table.add_row("Date Range", stats["date_range"])
    summary_table.add_row("Avg CP Loss/Game", f"{stats['avg_cp_loss_per_game']}cp")
    summary_table.add_row("Inaccuracies", str(stats["inaccuracies"]))
    summary_table.add_row("Mistakes", str(stats["mistakes"]))
    summary_table.add_row("Blunders", str(stats["blunders"]))
    console.print(summary_table)
    console.print()

    # Tactical themes
    themes = report_data["themes"]
    if themes:
        theme_table = Table(title="Top Missed Tactical Themes")
        theme_table.add_column("Theme", style="cyan")
        theme_table.add_column("Count", justify="right")
        theme_table.add_column("Avg CP Loss", justify="right")
        theme_table.add_column("Example", style="dim")
        for t in themes[:10]:
            theme_table.add_row(
                t["theme"],
                str(t["count"]),
                f"{t['avg_cp_loss']}cp",
                f"vs {t['example_opponent']}, move {t['example_move']}",
            )
        console.print(theme_table)
        console.print()

    # Phase analysis
    phases = report_data["phases"]
    phase_table = Table(title="Weakness by Game Phase")
    phase_table.add_column("Phase", style="cyan")
    phase_table.add_column("Mistakes", justify="right")
    phase_table.add_column("Mistake Rate", justify="right")
    phase_table.add_column("Top Theme")
    for phase in ["opening", "middlegame", "endgame"]:
        if phase in phases:
            p = phases[phase]
            phase_table.add_row(
                phase.title(),
                str(p["total_mistakes"]),
                f"{p['mistake_rate']}%",
                p["top_theme"],
            )
    console.print(phase_table)
    console.print()

    # Time pressure
    time_data = report_data["time_pressure"]
    if time_data:
        time_table = Table(title="Time Pressure Analysis")
        time_table.add_column("Clock", style="cyan")
        time_table.add_column("Mistakes", justify="right")
        time_table.add_column("Avg CP Loss", justify="right")
        time_table.add_column("Top Theme")
        for label in ["plenty (>3 min)", "moderate (1-3 min)", "time pressure (<1 min)"]:
            if label in time_data:
                td = time_data[label]
                time_table.add_row(
                    label,
                    str(td["mistake_count"]),
                    f"{td['avg_cp_loss']}cp",
                    td["top_theme"],
                )
        console.print(time_table)
        console.print()

    # Opening performance
    openings = report_data["openings"]
    if openings:
        opening_table = Table(title="Opening Performance")
        opening_table.add_column("Opening", style="cyan")
        opening_table.add_column("Games", justify="right")
        opening_table.add_column("W/L/D", justify="right")
        opening_table.add_column("Avg CP Loss", justify="right")
        opening_table.add_column("Mistakes/Game", justify="right")
        for o in openings[:10]:
            opening_table.add_row(
                o["opening"][:40],
                str(o["games"]),
                f"{o['wins']}/{o['losses']}/{o['draws']}",
                f"{o['avg_cp_loss']}cp",
                str(o["mistakes_per_game"]),
            )
        console.print(opening_table)
        console.print()

    # Verbose: detailed mistake log
    if verbose and report_data.get("all_mistakes"):
        console.print(Panel("[bold]Detailed Mistake Log[/bold]", expand=False))
        for i, m in enumerate(report_data["all_mistakes"], 1):
            cls = m.get("classification", {})
            severity_color = {"blunder": "red", "mistake": "yellow", "inaccuracy": "blue"}.get(
                m.get("severity", ""), "white"
            )
            console.print(
                f"  [{severity_color}]#{i}[/{severity_color}] "
                f"Move {m.get('move_number', '?')}: "
                f"[bold]{m.get('player_move', '?')}[/bold] "
                f"(best: {m.get('best_move', '?')}) "
                f"- {m.get('eval_swing', 0)}cp loss "
                f"[{m.get('severity', '')}]"
            )
            if cls.get("primary_theme"):
                console.print(
                    f"    Theme: {cls['primary_theme'].replace('_', ' ').title()}"
                )
            if cls.get("explanation"):
                console.print(f"    {cls['explanation']}")
            if cls.get("lesson"):
                console.print(f"    [italic]Lesson: {cls['lesson']}[/italic]")
            console.print(f"    vs {m.get('opponent', '?')} | {m.get('opening_name', '?')}")
            console.print()

    # AI Pattern Analysis
    if report_data.get("ai_analysis"):
        console.print(Panel("[bold]AI Pattern Analysis[/bold]", expand=False))
        console.print(report_data["ai_analysis"])
        console.print()


def _render_markdown(report_data, username, verbose=False):
    """Render report as a shareable markdown file aimed at a general audience."""
    lines = []
    stats = report_data["summary"]
    generated = datetime.now(timezone.utc).strftime("%B %d, %Y")

    lines.append(f"# Chess Analysis Report for **{username}**\n")
    lines.append(f"*Generated on {generated} — covering {stats['total_games']} games "
                 f"({stats['date_range']})*\n")

    # ── Summary ──
    lines.append("## Overview\n")
    lines.append(f"Across **{stats['total_games']} games** and **{stats['total_moves']} moves**, "
                 f"the analysis found **{stats['total_mistakes']}** moments where a significantly "
                 f"better move was available:\n")
    lines.append(f"- **{stats['inaccuracies']}** small inaccuracies (minor missed opportunities)")
    lines.append(f"- **{stats['mistakes']}** mistakes (meaningful advantage lost)")
    lines.append(f"- **{stats['blunders']}** blunders (game-changing errors)")
    lines.append(f"\nOn average, each game had roughly "
                 f"**{stats['avg_cp_loss_per_game']} centipawns** of positional value lost "
                 f"— think of 100 centipawns as roughly the value of one pawn.\n")

    # ── Themes ──
    themes = report_data["themes"]
    if themes:
        lines.append("## Most Common Tactical Patterns Missed\n")
        lines.append("These are the types of ideas that were most frequently overlooked:\n")
        lines.append("| Pattern | Times Missed | Avg Value Lost | First Seen |")
        lines.append("|---------|:------------:|:--------------:|------------|")
        for t in themes[:10]:
            lines.append(
                f"| {t['theme']} | {t['count']} | ~{t['avg_cp_loss'] / 100:.1f} pawns | "
                f"vs {t['example_opponent']}, move {t['example_move']} |"
            )
        lines.append("")

    # ── Phases ──
    phases = report_data["phases"]
    lines.append("## Where Mistakes Happen in the Game\n")
    lines.append("| Phase | Mistakes | Error Rate | Most Common Issue |")
    lines.append("|-------|:--------:|:----------:|-------------------|")
    for phase in ["opening", "middlegame", "endgame"]:
        if phase in phases:
            p = phases[phase]
            lines.append(
                f"| {phase.title()} | {p['total_mistakes']} | {p['mistake_rate']}% | {p['top_theme']} |"
            )
    lines.append("")

    # ── Time pressure ──
    time_data = report_data["time_pressure"]
    if time_data:
        lines.append("## How Time Pressure Affects Play\n")
        lines.append("| Time Remaining | Mistakes | Avg Value Lost | Most Common Issue |")
        lines.append("|----------------|:--------:|:--------------:|-------------------|")
        for label in ["plenty (>3 min)", "moderate (1-3 min)", "time pressure (<1 min)"]:
            if label in time_data:
                td = time_data[label]
                lines.append(
                    f"| {label.title()} | {td['mistake_count']} | "
                    f"~{td['avg_cp_loss'] / 100:.1f} pawns | {td['top_theme']} |"
                )
        lines.append("")

    # ── Openings ──
    openings = report_data["openings"]
    if openings:
        lines.append("## Performance by Opening\n")
        lines.append("| Opening | Games | Record (W/L/D) | Avg Value Lost | Mistakes/Game |")
        lines.append("|---------|:-----:|:--------------:|:--------------:|:-------------:|")
        for o in openings[:10]:
            lines.append(
                f"| {o['opening'][:40]} | {o['games']} | "
                f"{o['wins']}/{o['losses']}/{o['draws']} | "
                f"~{o['avg_cp_loss'] / 100:.1f} pawns | {o['mistakes_per_game']} |"
            )
        lines.append("")

    # ── Detailed mistake log ──
    if verbose and report_data.get("all_mistakes"):
        lines.append("## Detailed Mistake Log\n")
        for i, m in enumerate(report_data["all_mistakes"], 1):
            cls = m.get("classification", {})
            severity = m.get("severity", "")
            severity_label = {"blunder": "BLUNDER", "mistake": "Mistake", "inaccuracy": "Inaccuracy"}.get(
                severity, severity
            )
            lines.append(
                f"**#{i}** Move {m.get('move_number', '?')}: "
                f"played **{m.get('player_move', '?')}** "
                f"(best was {m.get('best_move', '?')}) "
                f"— lost ~{m.get('eval_swing', 0) / 100:.1f} pawns [{severity_label}]"
            )
            if cls.get("primary_theme"):
                lines.append(f"- Pattern: {cls['primary_theme'].replace('_', ' ').title()}")
            if cls.get("explanation"):
                lines.append(f"- {cls['explanation']}")
            if cls.get("lesson"):
                lines.append(f"- *Takeaway: {cls['lesson']}*")
            lines.append(f"- Opponent: {m.get('opponent', '?')} | Opening: {m.get('opening_name', '?')}")
            lines.append("")

    # ── AI Analysis ──
    if report_data.get("ai_analysis"):
        lines.append("## AI Insights & Training Recommendations\n")
        lines.append(report_data["ai_analysis"])
        lines.append("")

    # ── Glossary ──
    lines.append("---\n")
    lines.append("### How to read this report\n")
    lines.append("- **Centipawn (cp):** A unit for measuring advantage in chess. "
                 "100 cp = roughly one pawn's worth of advantage.")
    lines.append("- **Inaccuracy:** A move that lets a small advantage slip (100-200 cp lost).")
    lines.append("- **Mistake:** A move that loses a meaningful amount of advantage (200-300 cp lost).")
    lines.append("- **Blunder:** A move that loses a major advantage or the game (300+ cp lost).")
    lines.append("- **Opening / Middlegame / Endgame:** The three phases of a chess game. "
                 "The opening covers the first ~10 moves, the endgame begins when most pieces "
                 "are traded off, and the middlegame is everything in between.")
    lines.append("")

    content = "\n".join(lines)
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(config.OUTPUT_DIR, f"report_{username}.md")
    with open(path, "w") as f:
        f.write(content)
    return path


def generate_report(analysis_results, username, output_format="terminal", verbose=False):
    """Generate the full weakness report.

    Args:
        analysis_results: List of game analysis dicts (with classifications).
        username: Chess.com username.
        output_format: 'terminal', 'md', or 'json'.
        verbose: Whether to include detailed mistake log.

    Returns:
        Path to output file (for md/json) or None (for terminal).
    """
    console = Console()

    all_mistakes = _collect_all_mistakes(analysis_results)

    # Build report data
    console.print("[dim]Computing statistics...[/dim]")
    report_data = {
        "summary": _summary_stats(analysis_results, all_mistakes),
        "themes": _theme_analysis(all_mistakes),
        "phases": _phase_analysis(all_mistakes, analysis_results),
        "time_pressure": _time_pressure_analysis(all_mistakes),
        "openings": _opening_analysis(analysis_results),
    }

    if verbose:
        report_data["all_mistakes"] = all_mistakes

    # AI pattern analysis (only if we have classified mistakes)
    classified_count = sum(1 for m in all_mistakes if m.get("classification"))
    if classified_count > 0:
        console.print("[dim]Running AI pattern analysis with Claude Opus...[/dim]")
        try:
            report_data["ai_analysis"] = _ai_pattern_analysis(analysis_results, all_mistakes)
        except Exception as e:
            console.print(f"[yellow]Warning: AI analysis failed: {e}[/yellow]")
            report_data["ai_analysis"] = None
    else:
        report_data["ai_analysis"] = None

    # Render
    if output_format == "terminal":
        _render_terminal(report_data, verbose=verbose)
        # Always save a shareable markdown report alongside terminal output
        path = _render_markdown(report_data, username, verbose=verbose)
        console.print(f"\n[green]Shareable report saved to {path}[/green]")
        return path
    elif output_format == "md":
        path = _render_markdown(report_data, username, verbose=verbose)
        console.print(f"[green]Report saved to {path}[/green]")
        return path
    elif output_format == "json":
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        path = os.path.join(config.OUTPUT_DIR, f"report_{username}.json")
        with open(path, "w") as f:
            json.dump(report_data, f, indent=2)
        console.print(f"[green]Report saved to {path}[/green]")
        return path
    else:
        console.print(f"[red]Unknown output format: {output_format}[/red]")
        return None
