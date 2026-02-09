import json
import os
import time

import anthropic
from rich.progress import Progress, SpinnerColumn, TextColumn

import config


def _load_system_prompt():
    """Load the system prompt from the prompts directory."""
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "system_prompt.txt")
    with open(prompt_path, "r") as f:
        return f.read()


def _build_user_message(mistake, game_result):
    """Build the user message for a single mistake classification."""
    clock_str = f"{mistake['clock_seconds']:.0f}s" if mistake.get("clock_seconds") is not None else "N/A"

    # Describe eval swing
    swing = mistake["eval_swing"]
    if abs(mistake["eval_before"]) >= 9000 or abs(mistake["eval_after"]) >= 9000:
        swing_desc = f"{swing}cp (missed forced mate)"
    else:
        swing_desc = f"{swing}cp"

    return (
        f"Position (FEN): {mistake['fen']}\n"
        f"Player's move: {mistake['player_move']}\n"
        f"Engine best move: {mistake['best_move']}\n"
        f"Eval swing: {swing_desc}\n"
        f"Player color: {game_result['player_color']}\n"
        f"Move number: {mistake['move_number']}\n"
        f"Clock remaining: {clock_str}\n"
        f"Game phase: {mistake['game_phase']}"
    )


def _build_batch_jsonl(analysis_results):
    """Build JSONL lines for the Claude Batch API.

    Returns a list of (custom_id, jsonl_line) tuples and a mapping dict.
    """
    system_prompt = _load_system_prompt()
    lines = []
    id_map = {}  # custom_id -> (game_index, mistake_index)

    for game_idx, game_result in enumerate(analysis_results):
        for mistake_idx, mistake in enumerate(game_result.get("mistakes", [])):
            custom_id = f"game_{game_idx}_move_{mistake['move_number']}"
            # Ensure unique custom_id
            if custom_id in id_map:
                custom_id = f"game_{game_idx}_move_{mistake['move_number']}_{mistake_idx}"

            id_map[custom_id] = (game_idx, mistake_idx)

            request = {
                "custom_id": custom_id,
                "params": {
                    "model": config.CLAUDE_MODEL,
                    "max_tokens": config.MAX_TOKENS,
                    "system": [
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    "messages": [
                        {
                            "role": "user",
                            "content": _build_user_message(mistake, game_result),
                        }
                    ],
                },
            }
            lines.append((custom_id, json.dumps(request)))

    return lines, id_map


def classify_mistakes(analysis_results, username):
    """Submit mistakes to Claude Batch API for classification.

    Returns the analysis_results list with classification data added to each mistake.
    """
    # Build batch JSONL
    lines, id_map = _build_batch_jsonl(analysis_results)

    if not lines:
        print("No mistakes to classify.")
        return analysis_results

    # Write JSONL file
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    jsonl_path = os.path.join(config.OUTPUT_DIR, f"batch_{username}.jsonl")
    with open(jsonl_path, "w") as f:
        for _, line in lines:
            f.write(line + "\n")

    # Submit batch
    client = anthropic.Anthropic()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
    ) as progress:
        task = progress.add_task("Submitting batch to Claude API...", total=None)

        # Create the batch using the file
        batch = client.messages.batches.create(
            requests=[json.loads(line) for _, line in lines]
        )
        batch_id = batch.id
        progress.update(task, description=f"Batch {batch_id} submitted. Polling for results...")

        # Poll until complete
        while True:
            batch_status = client.messages.batches.retrieve(batch_id)
            status = batch_status.processing_status

            counts = batch_status.request_counts
            progress.update(
                task,
                description=(
                    f"Batch {batch_id}: {status} "
                    f"(succeeded={counts.succeeded}, "
                    f"errored={counts.errored}, "
                    f"processing={counts.processing})"
                ),
            )

            if status == "ended":
                break

            time.sleep(30)

    # Retrieve results
    classifications = {}
    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id

        if result.result.type == "succeeded":
            message = result.result.message
            text = message.content[0].text if message.content else ""
            try:
                classification = json.loads(text)
                classifications[custom_id] = classification
            except json.JSONDecodeError:
                print(f"Warning: Invalid JSON response for {custom_id}: {text[:100]}")
        else:
            error_type = result.result.type
            print(f"Warning: Request {custom_id} failed with status: {error_type}")

    # Map classifications back to mistakes
    for custom_id, classification in classifications.items():
        if custom_id in id_map:
            game_idx, mistake_idx = id_map[custom_id]
            if game_idx < len(analysis_results):
                mistakes = analysis_results[game_idx].get("mistakes", [])
                if mistake_idx < len(mistakes):
                    mistakes[mistake_idx]["classification"] = classification

    return analysis_results


def save_classifications(analysis_results, username):
    """Save classified analysis results to JSON file."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(config.OUTPUT_DIR, f"classifications_{username}.json")
    with open(path, "w") as f:
        json.dump(analysis_results, f, indent=2)
    return path


def load_classifications(username):
    """Load cached classification results."""
    path = os.path.join(config.OUTPUT_DIR, f"classifications_{username}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)
