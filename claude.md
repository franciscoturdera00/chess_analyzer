# Chess Weakness Analyzer

CLI tool that fetches Chess.com games, analyzes them with Stockfish, and uses Claude's Batch API to classify tactical/positional weaknesses into a detailed report.

## Architecture

4-step pipeline: **Fetch** (Chess.com API) → **Analyze** (Stockfish) → **Classify** (Claude Batch API) → **Report** (terminal + shareable markdown)

Key modules:
- `main.py` — CLI entry point and pipeline orchestration
- `fetcher.py` — Chess.com game archive fetching with local caching
- `analyzer.py` — Parallel Stockfish analysis (ProcessPoolExecutor)
- `classifier.py` — Claude Batch API for mistake classification with prompt caching
- `reporter.py` — Statistics aggregation, pattern analysis (Claude Opus), output formatting. Terminal output always auto-saves a non-developer-friendly markdown report to `output/report_<username>.md`
- `config.py` — Centralized configuration (env vars + defaults)
- `prompts/system_prompt.txt` — Chess analysis classification prompt

## Setup

```bash
pip install -r requirements.txt
```

Requires:
- Python 3.11+
- Stockfish binary installed and in PATH (or set `STOCKFISH_PATH`)
- Anthropic API key set as `ANTHROPIC_API_KEY`

Copy `.env.example` to `.env` and fill in values.

## Running

```bash
python main.py --username <chess.com-username> [--months 3] [--time-control blitz rapid] [--depth 18] [--output terminal]
```

The default `terminal` output also auto-saves a shareable markdown report to `output/report_<username>.md` (written for a non-technical audience with values in pawns and a glossary). Use `--output md` to skip terminal display and only write the file, or `--output json` for raw data.

Use `--skip-analysis` to reuse cached Stockfish results from a previous run.

## Code Conventions

- Python with snake_case naming, private functions prefixed with `_`
- Uses `rich` for terminal output and progress bars
- Intermediate results cached as JSON in `output/` directory
- Error handling with graceful fallbacks (continues if single game fails)
- Parallel processing for Stockfish analysis (CPU count - 1 workers)

## Key Thresholds (in config.py)

- Mistake: ≥100 centipawn eval swing
- Blunder: ≥300 centipawn eval swing
- Stockfish depth: 18

## Environment Variables

- `ANTHROPIC_API_KEY` — required for Claude classification and pattern analysis
- `STOCKFISH_PATH` — path to Stockfish binary (default: `stockfish` in PATH)
- `CHESS_USERNAME` — default Chess.com username
