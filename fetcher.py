import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import unquote

import requests
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

import config


USER_AGENT = "ChessWeaknessAnalyzer/1.0 (github.com/chess-weakness-analyzer)"


def _month_range(months_back):
    """Generate (year, month) tuples for the last N months including current."""
    now = datetime.now(timezone.utc)
    result = []
    year, month = now.year, now.month
    for _ in range(months_back):
        result.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return result


def _cache_path(username, year, month):
    return os.path.join(config.OUTPUT_DIR, f"game-archive-{username}-{year}-{month:02d}.json")


def _is_current_month(year, month):
    now = datetime.now(timezone.utc)
    return year == now.year and month == now.month


def _parse_opening_name(eco_url):
    """Parse opening name from Chess.com eco URL field.

    Example: 'https://www.chess.com/openings/Sicilian-Defense-Najdorf-Variation'
    Returns: 'Sicilian Defense Najdorf Variation'
    """
    if not eco_url:
        return "Unknown"
    try:
        # Get the last path segment and convert hyphens to spaces
        path = eco_url.rstrip("/").split("/")[-1]
        name = unquote(path).replace("-", " ")
        return name if name else "Unknown"
    except Exception:
        return "Unknown"


def fetch_games(username, months=None, time_controls=None):
    """Fetch games from Chess.com API for the given username.

    Returns a list of game dicts with parsed opening names added.
    """
    if months is None:
        months = config.MAX_MONTHS
    if time_controls is None:
        time_controls = config.TIME_CONTROLS

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    month_list = _month_range(months)
    all_games = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
    ) as progress:
        task = progress.add_task("Fetching games...", total=len(month_list))

        for year, month in month_list:
            cache_file = _cache_path(username, year, month)
            current = _is_current_month(year, month)

            # Use cache if available and not current month
            if os.path.exists(cache_file) and not current:
                with open(cache_file, "r") as f:
                    data = json.load(f)
                progress.update(task, advance=1, description=f"Loaded cached {year}-{month:02d}")
            else:
                url = f"https://api.chess.com/pub/player/{username}/games/{year}/{month:02d}"
                progress.update(task, description=f"Fetching {year}-{month:02d}...")

                resp = requests.get(url, headers={"User-Agent": USER_AGENT})
                if resp.status_code == 404:
                    progress.console.print(f"[yellow]Warning: No games found for {year}-{month:02d} (404)[/yellow]")
                    progress.update(task, advance=1)
                    time.sleep(1)
                    continue
                if resp.status_code == 429:
                    progress.console.print("[yellow]Rate limited, waiting 10s...[/yellow]")
                    time.sleep(10)
                    resp = requests.get(url, headers={"User-Agent": USER_AGENT})

                resp.raise_for_status()
                data = resp.json()

                # Cache the response
                with open(cache_file, "w") as f:
                    json.dump(data, f)

                progress.update(task, advance=1)
                time.sleep(1)  # Respect rate limits

            # Filter and process games
            games = data.get("games", [])
            for game in games:
                # Filter by rules (standard chess only)
                if game.get("rules") != "chess":
                    continue

                # Filter by time control
                if game.get("time_class") not in time_controls:
                    continue

                # Parse opening name from eco URL
                game["opening_name"] = _parse_opening_name(game.get("eco", ""))

                all_games.append(game)

    return all_games
