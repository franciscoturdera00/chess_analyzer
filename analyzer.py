import io
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor

import chess
import chess.engine
import chess.pgn
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

import config


# Piece values for material calculation (non-pawn, non-king)
PIECE_VALUES = {
    chess.QUEEN: 9,
    chess.ROOK: 5,
    chess.BISHOP: 3,
    chess.KNIGHT: 3,
}


def _calculate_material(board):
    """Calculate total non-pawn, non-king material on the board (both sides)."""
    total = 0
    for piece_type, value in PIECE_VALUES.items():
        total += len(board.pieces(piece_type, chess.WHITE)) * value
        total += len(board.pieces(piece_type, chess.BLACK)) * value
    return total


def _count_captures(move_list, board_initial):
    """Count how many captures of minor/major pieces have occurred."""
    board = board_initial.copy()
    captures = 0
    for move in move_list:
        if board.is_capture(move):
            captured = board.piece_at(move.to_square)
            if captured and captured.piece_type in PIECE_VALUES:
                captures += 1
        board.push(move)
    return captures


def _determine_phase(board, move_number, moves_so_far, initial_board):
    """Determine game phase based on material and move number.

    Opening: move number <= 10 AND no more than 2 minor/major pieces captured
    Middlegame: not opening and total non-pawn material > 24
    Endgame: total non-pawn material <= 24
    """
    material = _calculate_material(board)

    if move_number <= 10:
        captures = _count_captures(moves_so_far, initial_board)
        if captures <= 2:
            return "opening"

    if material <= 24:
        return "endgame"

    return "middlegame"


def _parse_clock(comment):
    """Parse clock time from PGN comment like '{[%clk 0:05:23]}'.

    Returns seconds remaining or None.
    """
    if not comment:
        return None
    match = re.search(r'\[%clk (\d+):(\d+):(\d+(?:\.\d+)?)\]', comment)
    if match:
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)
    return None


def _score_to_cp(score, player_color):
    """Convert a chess.engine score to centipawns from the player's perspective.

    Mate scores are converted to ±10000.
    """
    pov = score.white() if player_color == chess.WHITE else score.black()
    if pov.is_mate():
        mate_in = pov.mate()
        if mate_in > 0:
            return 10000
        else:
            return -10000
    return pov.score()


def _analyze_single_game(args):
    """Analyze a single game with Stockfish. Designed to run in a subprocess."""
    game_data, username, stockfish_path, depth, threads, hash_mb, threshold = args

    pgn_str = game_data.get("pgn", "")
    if not pgn_str:
        return None

    try:
        game = chess.pgn.read_game(io.StringIO(pgn_str))
    except Exception:
        return None

    if game is None:
        return None

    # Determine player color
    white_player = game_data.get("white", {})
    black_player = game_data.get("black", {})
    if white_player.get("username", "").lower() == username.lower():
        player_color = chess.WHITE
        player_info = white_player
        opponent_info = black_player
    elif black_player.get("username", "").lower() == username.lower():
        player_color = chess.BLACK
        player_info = black_player
        opponent_info = white_player
    else:
        return None

    # Determine result from player's perspective
    result_str = player_info.get("result", "")

    # Start Stockfish
    try:
        engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
        engine.configure({"Threads": threads, "Hash": hash_mb})
    except Exception as e:
        return {"error": f"Stockfish failed to start: {e}", "game_url": game_data.get("url", "")}

    board = game.board()
    initial_board = board.copy()
    mistakes = []
    moves_played = []
    total_cp_loss = 0
    player_moves_count = 0
    move_number = 0
    prev_eval = None

    try:
        node = game
        for node in game.mainline():
            move = node.move
            is_player_turn = board.turn == player_color

            if is_player_turn:
                move_number_display = board.fullmove_number

                # Eval BEFORE player's move (at current position)
                info_before = engine.analyse(board, chess.engine.Limit(depth=depth))
                eval_before = _score_to_cp(info_before["score"], player_color)

                # Make the player's move
                san_move = board.san(move)
                best_move_obj = info_before.get("pv", [None])[0]
                best_san = board.san(best_move_obj) if best_move_obj else "?"

                board.push(move)
                moves_played.append(move)

                # Eval AFTER player's move
                info_after = engine.analyse(board, chess.engine.Limit(depth=depth))
                eval_after = _score_to_cp(info_after["score"], player_color)

                # Eval swing (positive = loss for player)
                swing = eval_before - eval_after
                if swing < 0:
                    swing = 0  # Player improved the position (or maintained)

                player_moves_count += 1
                total_cp_loss += swing

                if swing >= threshold:
                    # Parse clock from the node comment
                    clock = _parse_clock(node.comment)

                    # Determine game phase at the position BEFORE the move
                    # We need to go back one to check the position before the move
                    temp_board = board.copy()
                    temp_board.pop()
                    phase = _determine_phase(temp_board, move_number_display, moves_played[:-1], initial_board)

                    # Classify severity
                    if swing >= config.BLUNDER_THRESHOLD:
                        severity = "blunder"
                    elif swing >= config.EVAL_SWING_THRESHOLD * 2:
                        severity = "mistake"
                    else:
                        severity = "inaccuracy"

                    mistakes.append({
                        "fen": temp_board.fen(),
                        "player_move": san_move,
                        "best_move": best_san,
                        "eval_before": eval_before,
                        "eval_after": eval_after,
                        "eval_swing": swing,
                        "move_number": move_number_display,
                        "game_phase": phase,
                        "clock_seconds": clock,
                        "severity": severity,
                    })
            else:
                board.push(move)
                moves_played.append(move)

    except Exception as e:
        # If analysis crashes mid-game, return what we have so far
        pass
    finally:
        engine.quit()

    avg_cp_loss = total_cp_loss / player_moves_count if player_moves_count > 0 else 0

    return {
        "game_url": game_data.get("url", ""),
        "time_control": game_data.get("time_control", ""),
        "time_class": game_data.get("time_class", ""),
        "player_color": "white" if player_color == chess.WHITE else "black",
        "player_rating": player_info.get("rating", 0),
        "opponent": opponent_info.get("username", "Unknown"),
        "opponent_rating": opponent_info.get("rating", 0),
        "result": result_str,
        "opening_name": game_data.get("opening_name", "Unknown"),
        "eco": game_data.get("eco", ""),
        "end_time": game_data.get("end_time", 0),
        "total_moves": player_moves_count,
        "avg_cp_loss": round(avg_cp_loss, 1),
        "mistakes": mistakes,
    }


def analyze_games(games, username, stockfish_path=None, depth=None, threshold=None):
    """Analyze all games with Stockfish using parallel processing.

    Returns a list of analysis results.
    """
    if stockfish_path is None:
        stockfish_path = config.STOCKFISH_PATH
    if depth is None:
        depth = config.STOCKFISH_DEPTH
    if threshold is None:
        threshold = config.EVAL_SWING_THRESHOLD

    workers = max(1, os.cpu_count() - 1)

    # Prepare args for each game
    args_list = [
        (game, username, stockfish_path, depth, config.STOCKFISH_THREADS, config.STOCKFISH_HASH, threshold)
        for game in games
    ]

    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("Analyzing games with Stockfish...", total=len(games))

        with ProcessPoolExecutor(max_workers=workers) as executor:
            for result in executor.map(_analyze_single_game, args_list):
                if result is not None:
                    results.append(result)
                progress.update(task, advance=1)

    return results


def save_analysis(results, username):
    """Save analysis results to JSON file."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(config.OUTPUT_DIR, f"analysis_{username}.json")
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    return path


def load_analysis(username):
    """Load cached analysis results from JSON file."""
    path = os.path.join(config.OUTPUT_DIR, f"analysis_{username}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)
