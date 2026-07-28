"""Event-driven cricket retrain: checks whether Cricsheet's The Hundred /
MLC archives have NEW completed matches since the last retrain (hash of the
downloaded raw JSON directories), and only rebuilds features + retrains +
redeploys if so -- mirrors scripts/check_and_retrain_soccer.py's
hash-and-skip pattern exactly, adapted for Cricsheet's "one zip per
competition" shape instead of per-league season CSVs.

This is also how the site owner's ask ("this cricket sport should run from
results which predicted wrong") is served going forward: every retrain
re-runs build_training_dataset.py over ALL matches including newly finished
ones, so newly available real results (and whether the model's prior
prediction for them was right or wrong) flow into the next holdout report
and the fixtures page's actual-vs-predicted display, the same way
mlb/npb track records pick up new games.

Run: venv_train\\Scripts\\python.exe scripts\\cricket\\check_and_retrain_cricket.py
"""
import glob
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cricket_config import RAW_DIR  # noqa: E402

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "deploy", "cricket_retrain_state.json")


def _raw_data_hash():
    """Hashes every downloaded match JSON file's name + size together --
    cheap, and changes the moment Cricsheet's zip gains/updates a match
    file, without needing to parse JSON just to detect that."""
    h = hashlib.sha256()
    for path in sorted(glob.glob(os.path.join(RAW_DIR, "*", "*.json"))):
        h.update(os.path.basename(path).encode())
        h.update(str(os.path.getsize(path)).encode())
    return h.hexdigest()


def _load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f)


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    python = os.path.join(repo_root, "venv_train", "Scripts", "python.exe")
    scripts_dir = os.path.dirname(os.path.abspath(__file__))

    print("==> Refreshing Cricsheet archives (cheap -- one zip per competition)")
    subprocess.run([python, os.path.join(scripts_dir, "download_data.py")], check=True, cwd=scripts_dir)

    new_hash = _raw_data_hash()
    state = _load_state()
    if state.get("last_hash") == new_hash:
        print("==> No new completed matches since last check -- skipping retrain.")
        return

    print("==> New match data detected -- rebuilding features and retraining cricket models")
    subprocess.run([python, os.path.join(scripts_dir, "build_elo.py")], check=True, cwd=scripts_dir)
    subprocess.run([python, os.path.join(scripts_dir, "build_training_dataset.py")], check=True, cwd=scripts_dir)
    subprocess.run([python, os.path.join(scripts_dir, "train_ml_models.py")], check=True, cwd=scripts_dir)

    print("==> Running test suite as a safety gate before deploying")
    result = subprocess.run(
        [python, "-m", "pytest", "tests", "-q"], cwd=os.path.join(repo_root, "webapp"),
    )
    if result.returncode != 0:
        print("==> Tests FAILED -- refusing to deploy a broken model. State not updated, will retry next check.")
        sys.exit(1)

    print("==> Tests passed -- deploying to EC2")
    bash = r"C:\Program Files\Git\bin\bash.exe"
    subprocess.run([bash, os.path.join(repo_root, "deploy", "sync.sh"), "data/cricket"], check=True, cwd=repo_root)

    state["last_hash"] = new_hash
    _save_state(state)
    print("==> Done")


if __name__ == "__main__":
    main()
