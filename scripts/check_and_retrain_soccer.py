"""Event-driven soccer retrain: checks whether any league's current-season
results file has NEW completed matches since the last retrain, and only
retrains+redeploys if so -- instead of blindly retraining on a fixed
schedule regardless of whether any matches have actually gone live and
finished. Meant to run frequently (e.g. every 30-60 min) via Task
Scheduler; most runs will find nothing new and exit immediately.

Run: venv_train\\Scripts\\python.exe scripts\\check_and_retrain_soccer.py
"""
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leagues import LEAGUES, REPO_ROOT, league_seasons, resolve_league_paths  # noqa: E402

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", "soccer_retrain_state.json")


def _current_season_hash():
    """Hashes the current-season CSV for every league together -- any
    change (a completed match added a new row/result) changes the hash,
    which is a simple and reliable "did anything new actually happen"
    signal without needing to parse/diff match-by-match."""
    h = hashlib.sha256()
    for key, cfg in LEAGUES.items():
        seasons = league_seasons(cfg)
        current_season = seasons[-1]
        resolved = resolve_league_paths(cfg)
        path = os.path.join(
            resolved["data_dir"], f"{cfg['fd_code']}_{current_season.replace('/', '-')}.csv"
        )
        if os.path.exists(path):
            with open(path, "rb") as f:
                h.update(f.read())
        h.update(key.encode())
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
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    python = os.path.join(repo_root, "venv_train", "Scripts", "python.exe")
    scripts_dir = os.path.dirname(os.path.abspath(__file__))

    print("==> Refreshing current-season results (cheap -- just a CSV download per league)")
    subprocess.run([python, os.path.join(scripts_dir, "download_data.py")], check=True, cwd=scripts_dir)

    new_hash = _current_season_hash()
    state = _load_state()
    if state.get("last_hash") == new_hash:
        print("==> No new completed matches since last check -- skipping retrain.")
        return

    print("==> New match results detected -- retraining soccer models")
    subprocess.run([python, os.path.join(scripts_dir, "build_features.py")], check=True, cwd=scripts_dir)
    subprocess.run([python, os.path.join(scripts_dir, "train_models.py")], check=True, cwd=scripts_dir)
    subprocess.run([python, os.path.join(scripts_dir, "predict_pl.py")], check=True, cwd=scripts_dir)

    print("==> Running test suite as a safety gate before deploying")
    result = subprocess.run(
        [python, "-m", "pytest", "tests", "-q"], cwd=os.path.join(repo_root, "webapp"),
    )
    if result.returncode != 0:
        print("==> Tests FAILED -- refusing to deploy a broken model. State not updated, will retry next check.")
        sys.exit(1)

    print("==> Tests passed -- deploying to EC2")
    bash = r"C:\Program Files\Git\bin\bash.exe"
    # Ship both soccer data trees and model weights the Flask app loads.
    subprocess.run(
        [bash, os.path.join(repo_root, "deploy", "sync.sh"), "data", "model", "output_tables"],
        check=True,
        cwd=repo_root,
    )

    state["last_hash"] = new_hash
    _save_state(state)
    print("==> Done")


if __name__ == "__main__":
    main()
