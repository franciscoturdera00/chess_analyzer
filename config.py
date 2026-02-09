import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Chess.com username — set via CLI arg or env var
CHESSCOM_USERNAME = os.environ.get("CHESS_USERNAME", "")

# Path to stockfish binary — env var or assume it's in PATH
STOCKFISH_PATH = os.environ.get("STOCKFISH_PATH", "stockfish")

# Anthropic API key
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Analysis settings
EVAL_SWING_THRESHOLD = 100   # Minimum centipawn loss to flag as mistake (100cp = 1 pawn)
BLUNDER_THRESHOLD = 300      # Centipawn loss to flag as blunder
STOCKFISH_DEPTH = 18         # Engine analysis depth
STOCKFISH_THREADS = 2        # Engine threads per instance
STOCKFISH_HASH = 256         # Engine hash table MB

# Claude settings
CLAUDE_MODEL = "claude-haiku-4-5-20251001"  # Haiku for batch classification
ANALYSIS_MODEL = "claude-opus-4-6"          # Opus for final pattern analysis
MAX_TOKENS = 300             # Per classification response
ANALYSIS_MAX_TOKENS = 4096   # For final pattern analysis

# Game fetching
MAX_MONTHS = 3               # How many months back to fetch
TIME_CONTROLS = ["blitz", "rapid", "bullet"]  # Which time controls to include

# Output directory
OUTPUT_DIR = "output"
