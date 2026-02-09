# Claude Code Prompt

Read the full `chess-analyzer-spec.md` in this repo — it's a detailed spec for a Chess Weakness Analyzer tool.

Build the entire project following the spec exactly. Work through each component in this order:

1. `requirements.txt` — all dependencies
2. `config.py` — configuration with env var support
3. `fetcher.py` — Chess.com API game fetcher with local file caching
4. `analyzer.py` — Stockfish analysis with parallel processing via ProcessPoolExecutor
5. `prompts/system_prompt.txt` — the classification system prompt (copy from the spec)
6. `classifier.py` — Claude Batch API with prompt caching on the system prompt
7. `reporter.py` — report generation including the final Opus analysis call
8. `main.py` — CLI entry point tying it all together with rich progress bars

After building each component, test it in isolation before moving on. When everything is built, do a dry run with `--help` to make sure the CLI works.

Key things to get right:
- Stockfish evals must be from the player's perspective (negate for black)
- Handle mate scores by converting to large centipawn values
- Batch API JSONL must use `cache_control: {"type": "ephemeral"}` on the system message
- The final analysis call uses `claude-opus-4-6`, not Haiku
- Game phase detection is material-based, not just move number
- Use `rich.progress` for all long-running steps
- Parse opening names from the `eco` URL field in Chess.com's API response
