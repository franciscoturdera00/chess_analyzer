#!/usr/bin/env python3
"""Chess Weakness Analyzer — Find your tactical blind spots."""

import argparse
import json
import os
import sys

from rich.console import Console

import config
from fetcher import fetch_games
from analyzer import analyze_games, save_analysis, load_analysis
from classifier import classify_mistakes, save_classifications, load_classifications
from reporter import generate_report


def parse_args():
    parser = argparse.ArgumentParser(
        description="Chess Weakness Analyzer - Find your tactical blind spots",
    )
    parser.add_argument(
        "--username", "-u",
        default=config.CHESSCOM_USERNAME,
        help="Chess.com username (default: $CHESS_USERNAME env var)",
    )
    parser.add_argument(
        "--months", "-m",
        type=int,
        default=config.MAX_MONTHS,
        help=f"Months of games to analyze (default: {config.MAX_MONTHS})",
    )
    parser.add_argument(
        "--time-control", "-t",
        nargs="+",
        choices=["bullet", "blitz", "rapid", "daily"],
        default=None,
        help="Filter by time control (default: bullet blitz rapid)",
    )
    parser.add_argument(
        "--depth", "-d",
        type=int,
        default=config.STOCKFISH_DEPTH,
        help=f"Stockfish analysis depth (default: {config.STOCKFISH_DEPTH})",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=config.EVAL_SWING_THRESHOLD,
        help=f"Centipawn loss threshold for flagging mistakes (default: {config.EVAL_SWING_THRESHOLD})",
    )
    parser.add_argument(
        "--stockfish-path",
        default=config.STOCKFISH_PATH,
        help=f"Path to Stockfish binary (default: {config.STOCKFISH_PATH})",
    )
    parser.add_argument(
        "--output", "-o",
        choices=["terminal", "md", "json"],
        default="terminal",
        help="Output format (default: terminal)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Include detailed mistake log in report",
    )
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Skip Stockfish analysis, use cached results from previous run",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    console = Console()

    # Validate username
    if not args.username:
        console.print("[red]Error: Chess.com username is required.[/red]")
        console.print("Use --username flag or set CHESS_USERNAME environment variable.")
        sys.exit(1)

    username = args.username
    time_controls = args.time_control or config.TIME_CONTROLS

    console.print(f"[bold]Chess Weakness Analyzer[/bold]")
    console.print(f"Player: {username}")
    console.print(f"Months: {args.months} | Time controls: {', '.join(time_controls)}")
    console.print(f"Stockfish depth: {args.depth} | Threshold: {args.threshold}cp")
    console.print()

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    # Step 1: Fetch games
    console.print("[bold cyan]Step 1/4: Fetching games from Chess.com[/bold cyan]")
    games = fetch_games(username, months=args.months, time_controls=time_controls)
    console.print(f"Found {len(games)} games.\n")

    if not games:
        console.print("[yellow]No games found. Check username and filters.[/yellow]")
        sys.exit(0)

    # Step 2: Stockfish analysis
    console.print("[bold cyan]Step 2/4: Stockfish analysis[/bold cyan]")
    if args.skip_analysis:
        analysis_results = load_analysis(username)
        if analysis_results is None:
            console.print("[red]No cached analysis found. Run without --skip-analysis first.[/red]")
            sys.exit(1)
        console.print(f"Loaded cached analysis for {len(analysis_results)} games.\n")
    else:
        analysis_results = analyze_games(
            games,
            username,
            stockfish_path=args.stockfish_path,
            depth=args.depth,
            threshold=args.threshold,
        )
        save_path = save_analysis(analysis_results, username)
        console.print(f"Analysis saved to {save_path}\n")

    total_mistakes = sum(len(g.get("mistakes", [])) for g in analysis_results)
    console.print(f"Found {total_mistakes} mistakes across {len(analysis_results)} games.\n")

    if total_mistakes == 0:
        console.print("[green]No mistakes found! Either you played perfectly or the threshold is too high.[/green]")
        console.print("Try lowering --threshold (e.g., --threshold 50)")
        sys.exit(0)

    # Step 3: Claude classification
    console.print("[bold cyan]Step 3/4: Classifying mistakes with Claude[/bold cyan]")
    analysis_results = classify_mistakes(analysis_results, username)
    save_classifications(analysis_results, username)
    console.print(f"Classification complete.\n")

    # Step 4: Generate report
    console.print("[bold cyan]Step 4/4: Generating report[/bold cyan]")
    generate_report(
        analysis_results,
        username,
        output_format=args.output,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
