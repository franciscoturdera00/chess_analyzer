# Chess Weakness Analyzer

A Python CLI tool that fetches your Chess.com games, analyzes them with Stockfish to find mistakes, then uses the Claude Batch API (with prompt caching) to classify missed tactical and positional themes — generating a detailed weakness report.

## Architecture Overview

```
Chess.com API → Stockfish Analysis → Claude Batch API → Aggregation → Report
```

### Pipeline Steps

1. **Fetch**: Pull games from Chess.com's public API as PGN
2. **Analyze**: Run Stockfish on each position to find mistakes (eval swing > threshold)
3. **Classify**: Build a JSONL batch file and submit to Claude Batch API for tactical/positional classification
4. **Report**: Aggregate classifications and generate a detailed weakness report

## Tech Stack

- **Python 3.11+**
- **python-chess** — PGN parsing, board representation, Stockfish integration
- **Stockfish** — local engine for move evaluation (must be installed separately)
- **anthropic** Python SDK — for Claude Batch API with prompt caching
- **rich** — for terminal report formatting (optional, can fall back to plain text)

## Project Structure

```
chess-analyzer/
├── README.md
├── requirements.txt
├── config.py              # Configuration (thresholds, paths, API settings)
├── main.py                # CLI entry point
├── fetcher.py             # Chess.com API game fetcher
├── analyzer.py            # Stockfish analysis engine
├── classifier.py          # Claude Batch API classification
├── reporter.py            # Report generation
├── prompts/
│   └── system_prompt.txt  # System prompt for Claude classification
└── output/                # Generated reports and intermediate data
```

## Detailed Component Specs

### 1. `config.py`

Store all configuration:

```python
CHESSCOM_USERNAME = ""           # Set via CLI arg or env var CHESS_USERNAME
STOCKFISH_PATH = ""              # Path to stockfish binary, env var STOCKFISH_PATH
ANTHROPIC_API_KEY = ""           # env var ANTHROPIC_API_KEY

# Analysis settings
EVAL_SWING_THRESHOLD = 100      # Minimum centipawn loss to flag as mistake (100cp = 1 pawn)
BLUNDER_THRESHOLD = 300          # Centipawn loss to flag as blunder
STOCKFISH_DEPTH = 18             # Engine analysis depth
STOCKFISH_THREADS = 2            # Engine threads
STOCKFISH_HASH = 256             # Engine hash table MB

# Claude settings
CLAUDE_MODEL = "claude-haiku-4-5-20251001"  # Haiku is sufficient for classification
MAX_TOKENS = 300                 # Per classification response

# Game fetching
MAX_MONTHS = 3                   # How many months back to fetch
TIME_CONTROLS = ["blitz", "rapid", "bullet"]  # Which time controls to include
```

### 2. `fetcher.py` — Chess.com Game Fetcher

Use the Chess.com public API (no auth required):

- **Endpoint**: `https://api.chess.com/pub/player/{username}/games/{YYYY}/{MM}`
- Each API call returns **all games for an entire month**, so fetching 3 months = 3 requests
- Fetch games for the configured number of months
- Parse PGN data from the response
- Filter by `time_class` field if specified (API already provides `"daily"`, `"rapid"`, `"blitz"`, `"bullet"` — no need to derive from time_control)
- Filter out non-standard variants using the `rules` field (keep only `"chess"`)
- Return the raw game objects from the API — they already contain all needed metadata:
  - `pgn` (with clock comments)
  - `white` / `black` (username, rating, result)
  - `time_control`, `time_class`
  - `eco` (opening URL — parse opening name from this)
  - `accuracies` (Chess.com's own accuracy, if available)
  - `end_time`, `url`
- Add a `User-Agent` header to requests (Chess.com requires this)
- Add `time.sleep(1)` between requests to respect rate limits
- **Local caching**: Save each month's response as `output/game-archive-{username}-{YYYY-MM}.json`. On re-run, skip any month that already has a file on disk. Exception: always re-fetch the **current calendar month** (new games may have been played since last fetch).

### 3. `analyzer.py` — Stockfish Analysis

#### Data Already Available from Chess.com API

The monthly archive endpoint returns rich game objects. The spec should leverage these fields rather than re-derive them:

- **`white` / `black`**: Objects with `username`, `rating`, `result`, `@id`
- **`accuracies`**: `{ "white": float, "black": float }` — Chess.com's own accuracy scores (if previously calculated, not always present)
- **`eco`**: URL pointing to ECO opening code (e.g., `https://www.chess.com/openings/Sicilian-Defense...`) — parse the opening name from this
- **`time_control`**: PGN-standard time control string (e.g., `"600"` for 10min, `"180+2"` for 3+2)
- **`time_class`**: Already classified as `"daily"`, `"rapid"`, `"blitz"`, or `"bullet"` — no need to derive this
- **`rules`**: Game variant (`"chess"`, `"chess960"`, etc.) — filter out non-standard variants
- **`end_time`**: Unix timestamp of game end
- **`pgn`**: Full PGN with clock comments like `{[%clk 0:05:23]}`
- **`url`**: Direct link to the game on Chess.com

**Important**: `accuracies` is only present if Chess.com previously calculated them (e.g., if the user reviewed the game). Don't rely on it being present — our Stockfish analysis provides this independently.

#### Stockfish Analysis Pipeline

For each game:

1. Parse the PGN with `python-chess`
2. Replay the game move by move
3. At each position where it's the player's turn:
   - Run Stockfish to get the best move and evaluation
   - Compare the evaluation before and after the player's move
   - If the eval swing exceeds `EVAL_SWING_THRESHOLD`, flag it as a mistake
4. For each flagged mistake, record:
   - **FEN** of the position before the move
   - **Player's move** (in SAN notation)
   - **Best move** (Stockfish's recommendation, in SAN notation)
   - **Eval before** and **eval after** (in centipawns, from player's perspective)
   - **Eval swing** (centipawn loss)
   - **Move number**
   - **Game phase**: Determine based on material remaining on the board (count total non-pawn, non-king material using standard piece values: Q=9, R=5, B=3, N=3):
     - Opening: move number <= 10 AND no more than 2 minor/major pieces have been captured
     - Middlegame: not opening and total non-pawn material > 24 points (both sides combined)
     - Endgame: total non-pawn material <= 24 points (both sides combined)
   - **Clock time remaining** (if available from PGN clock comments)
   - **Game metadata**: time control, time class, opponent, result, rating, ECO/opening (all from the API response — do not re-derive these)

Output: A list of mistake objects ready for classification.

### 4. `classifier.py` — Claude Batch API Classification

#### System Prompt (`prompts/system_prompt.txt`)

This is the cached system prompt. It should be thorough since caching makes it cheap:

```
You are a chess position analyst. Given a chess position (FEN), the player's move, and the engine's best move, classify what tactical or positional theme the player missed.

## Tactical Themes

Classify the missed best move into one or more of these categories:

### Forks
- **Knight fork**: Knight attacks two or more pieces simultaneously
- **Royal fork**: Fork involving the king (forcing)
- **Queen fork**: Queen attacks two or more pieces
- **Pawn fork**: Pawn attacks two pieces
- **Bishop fork**: Bishop attacks two or more pieces along diagonals

### Pins and Skewers
- **Absolute pin**: Piece is pinned to the king (cannot legally move)
- **Relative pin**: Piece is pinned to a more valuable piece (can move but shouldn't)
- **Skewer**: Attack on a valuable piece that, when moved, exposes a less valuable piece behind it

### Discovered Attacks
- **Discovered attack**: Moving one piece reveals an attack from another piece
- **Discovered check**: Moving one piece reveals a check from another piece
- **Double check**: Two pieces give check simultaneously

### Mating Patterns
- **Back rank mate**: Checkmate on the 1st or 8th rank with a trapped king
- **Smothered mate**: Knight checkmate where the king is surrounded by its own pieces
- **Greek gift sacrifice**: Bishop sacrifice on h7/h2 leading to a mating attack
- **Missed checkmate**: Any missed forced checkmate (specify the pattern if recognizable)
- **Mating attack**: Missed continuation of a mating attack (not immediate mate)

### Material Tactics
- **Removal of defender**: Capturing or deflecting a piece that defends a key square or piece
- **Deflection**: Forcing a piece away from a critical defensive duty
- **Overloaded piece**: Exploiting a piece that has too many defensive responsibilities
- **Trapped piece**: The best move wins a piece that has no escape squares
- **Intermediate move (Zwischenzug)**: A surprising in-between move before the expected recapture
- **X-ray attack**: Attack through another piece along a line

### Pawn Tactics
- **Promotion threat**: Missed pawn push toward promotion or missed promotion
- **Passed pawn advance**: Failed to push a passed pawn
- **Pawn breakthrough**: Missed pawn sacrifice to create a passed pawn

### Positional Themes
- **Piece activity**: Missed move that significantly improves piece placement
- **Open file control**: Missed rook or queen placement on an open file
- **Weak square exploitation**: Missed occupation of a weak square in opponent's position
- **Exchange advantage**: Should have traded pieces (ahead in material, simplifying)
- **Exchange disadvantage**: Should NOT have traded (behind or equal, keeping complexity)
- **King safety neglect**: Missed a move to improve own king safety or exploit opponent's weak king
- **Endgame technique**: Missed known endgame principle (king activity, opposition, etc.)

## Response Format

Respond ONLY with valid JSON, no markdown, no backticks, no explanation outside the JSON:

{
  "primary_theme": "knight_fork",
  "secondary_theme": null,
  "sub_category": "royal_fork",
  "phase_context": "middlegame",
  "explanation": "After Nd5+, the knight simultaneously attacks the king on e7 and the rook on c3. The king must move, and then Nxc3 wins the exchange.",
  "difficulty": "intermediate",
  "lesson": "Look for knight outposts that attack multiple pieces, especially when the opponent's king is exposed."
}

- **primary_theme**: The main tactical/positional theme (use snake_case from the categories above)
- **secondary_theme**: A second theme if applicable, otherwise null
- **sub_category**: More specific classification if applicable, otherwise null
- **phase_context**: "opening", "middlegame", or "endgame"
- **explanation**: 2-3 sentence explanation of the missed tactic in plain English. Be specific about the pieces and squares involved.
- **difficulty**: "beginner", "intermediate", or "advanced"
- **lesson**: One actionable sentence the player can use to improve.
```

#### Batch API Implementation

1. **Build the batch JSONL file**: For each mistake position, create a request:
   ```json
   {
     "custom_id": "game_3_move_22",
     "params": {
       "model": "claude-haiku-4-5-20251001",
       "max_tokens": 300,
       "system": [
         {
           "type": "text",
           "text": "<contents of system_prompt.txt>",
           "cache_control": {"type": "ephemeral"}
         }
       ],
       "messages": [
         {
           "role": "user",
           "content": "Position (FEN): r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4\nPlayer's move: Bc4-b3\nEngine best move: Qh5xf7#\nEval swing: -9999cp (missed forced mate)\nPlayer color: white\nMove number: 4\nClock remaining: 4:32\nGame phase: opening"
         }
       ]
     }
   }
   ```

2. **Submit the batch** using the Anthropic Python SDK:
   ```python
   import anthropic
   client = anthropic.Anthropic()
   
   # Upload the JSONL file
   # Create the batch
   # Poll for completion
   # Download results
   ```

3. **Parse results**: Match each response back to its mistake via `custom_id`, parse the JSON classification.

4. **Error handling**: Some classifications may fail or return invalid JSON. Log these and skip them in the report.

### 5. `reporter.py` — Report Generation

Generate a comprehensive weakness report from the aggregated classifications.

#### Report Sections

**1. Summary Statistics**
- Total games analyzed
- Total moves analyzed
- Total mistakes found (by severity: inaccuracy/mistake/blunder)
- Average centipawn loss per game
- Date range of games analyzed

**2. Top Missed Tactical Themes** (ranked by frequency)
```
Theme                    Count    Avg CP Loss    Example
─────────────────────────────────────────────────────
Knight Fork              12       245cp          Game vs opponent1, move 22
Back Rank Mate           8        890cp          Game vs opponent2, move 35
Removal of Defender      6        180cp          Game vs opponent3, move 18
```

**3. Weakness by Game Phase**
- Opening accuracy %
- Middlegame accuracy %
- Endgame accuracy %
- Most common mistake type per phase

**4. Time Pressure Analysis**
- Average accuracy with >3 min remaining
- Average accuracy with 1-3 min remaining
- Average accuracy with <1 min remaining
- Most common mistake type under time pressure

**5. Opening Performance**
- List openings played (by ECO code or name if identifiable)
- Win/loss/draw rate per opening
- Average accuracy per opening

**6. Detailed Mistake Log** (optional, with --verbose flag)
- Each mistake with full context: position, moves, classification, explanation

**7. AI Pattern Analysis** (the key section)

After aggregating all classifications, send the full dataset to Claude (regular API, not batch) for a high-level pattern analysis. This is a single API call with a prompt like:

```
Here are all the mistakes from my last {N} chess games, classified by tactical theme:

{JSON dump of all classified mistakes with metadata: theme, phase, clock time, opening, eval swing, move number, difficulty}

Analyze this data and identify:
1. My biggest recurring blind spots (patterns, not just counts)
2. Correlations I might not notice (e.g., do I miss tactics more in certain openings? After certain move patterns? When I'm ahead/behind in material?)
3. Specific training recommendations ranked by impact
4. Any psychological patterns (e.g., time pressure mistakes, tilting after blunders, playing too safe when ahead)
5. A priority-ordered improvement plan: what should I work on first, second, third?

Be specific and cite actual examples from the data. Don't just say "work on forks" — say "you missed 8 knight forks, 6 of which were in rook+knight endgames when you had less than 90 seconds, suggesting you stop calculating knight moves under time pressure."
```

Use `claude-opus-4-6` for this call — this is where you want the strongest reasoning and pattern recognition. It's a single call so the cost is minimal relative to the value of the analysis.

The output of this analysis becomes the main narrative section of the report. The statistical tables provide the data backing, but this AI analysis is the actual value — it's what makes the report feel like a chess coach reviewed your games.

#### Report Output Formats
- **Terminal**: Pretty-printed using `rich` (default)
- **Markdown**: Save as `.md` file (with `--output md` flag)
- **JSON**: Raw data export (with `--output json` flag)

### 6. `main.py` — CLI Entry Point

Use `argparse` for the CLI:

```
usage: python main.py [options]

Chess Weakness Analyzer - Find your tactical blind spots

Required:
  --username, -u       Chess.com username

Optional:
  --months, -m         Months of games to analyze (default: 3)
  --time-control, -t   Filter by time control: bullet, blitz, rapid (default: all)
  --depth, -d          Stockfish analysis depth (default: 18)
  --threshold          Centipawn loss threshold for flagging mistakes (default: 100)
  --stockfish-path     Path to Stockfish binary (default: from $STOCKFISH_PATH or "stockfish")
  --output, -o         Output format: terminal, md, json (default: terminal)
  --verbose, -v        Include detailed mistake log in report
  --skip-analysis      Skip Stockfish analysis, use cached analysis from previous run
```

#### Caching / Intermediate Files

Save intermediate results so you can re-run parts of the pipeline without repeating expensive steps:

- `output/game-archive-{username}-{YYYY-MM}.json` — Raw Chess.com API responses per month (skipped on re-fetch if file exists, except current month)
- `output/analysis_{username}.json` — Stockfish analysis results cache (use `--skip-analysis` to reuse)
- `output/batch_{id}.jsonl` — Batch request file
- `output/classifications_{username}.json` — Claude classification results
- `output/report_{username}.{md|json}` — Final report

## Setup Instructions

```bash
# 1. Clone and install dependencies
pip install python-chess anthropic rich

# 2. Install Stockfish
# macOS: brew install stockfish
# Ubuntu: sudo apt install stockfish
# Or download from https://stockfishchess.org/download/

# 3. Set environment variables
export ANTHROPIC_API_KEY="your-api-key"
export STOCKFISH_PATH="/usr/local/bin/stockfish"  # optional if stockfish is in PATH
export CHESS_USERNAME="your-chess-com-username"    # optional, can use --username flag

# 4. Run
python main.py --username your_username --months 1 --output md
```

## Important Implementation Notes

### Stockfish Evaluation
- Always evaluate from the **player's perspective** (negate eval if playing black)
- Use `info["score"]` from python-chess's Stockfish integration
- Handle mate scores: `Mate(3)` means mate in 3, convert to a large centipawn value (e.g., 10000) for threshold comparison
- Set a time limit per position (e.g., 0.5 seconds) OR use depth limit — depth is more consistent

### Chess.com API
- No authentication needed
- Add a `User-Agent` header with contact info (Chess.com policy)
- Serial access is unlimited per Chess.com docs — just wait for each response before making the next request. Add `time.sleep(1)` between requests as a safety margin.
- If you get a 429, back off and retry — this only happens with parallel requests
- Games endpoint returns full game objects including PGN with clock comments

### Claude Batch API
- Use `cache_control: {"type": "ephemeral"}` on the system prompt for prompt caching
- Batch requests must be JSONL format
- Poll `client.batches.retrieve(batch_id)` until status is `ended`
- Results come as JSONL — parse each line and match on `custom_id`
- Haiku is the right model choice — this is a structured classification task

### Error Handling
- If Stockfish crashes on a position, skip it and log a warning
- If Claude returns invalid JSON for a classification, log it and skip
- If Chess.com API returns 404, the user probably doesn't exist
- If Chess.com API returns 429, back off and retry

### Performance Expectations
- Fetching: ~1 second per month of games (serial access is unlimited per Chess.com docs — only parallel requests get rate limited)
- Stockfish analysis: ~30-60 seconds per game at depth 18
- Claude batch: Minutes to process, polling every 30 seconds
- Total for 100 games: ~15-20 minutes end to end

### Parallelization
- Stockfish analysis is the bottleneck. Use Python's `concurrent.futures.ProcessPoolExecutor` to analyze multiple games in parallel.
- Default to `cpu_count - 1` workers. Each worker gets its own Stockfish instance.
- The fetcher and classifier are I/O bound and fast enough single-threaded.

### Progress Indicators
- This is a long-running CLI tool. Use `rich.progress` to show progress bars for:
  - Game fetching (X/Y months)
  - Stockfish analysis (X/Y games, with ETA)
  - Batch classification (polling status)
  - Report generation
