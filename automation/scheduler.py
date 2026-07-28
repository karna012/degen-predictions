"""
Daily automation for Degen Predictions. Runs ONCE per invocation --
recurrence is handled by a LOCAL scheduler (Windows Task Scheduler),
not by anything on the EC2 server. Training must never run on EC2.

Cycle per category (sports, crypto, stocks, real_estate, metals):
  1. Fetch latest data           (on this PC)
  2. Retrain models              (on this PC)
  3. Generate predictions        (on this PC)
  4. Track prediction accuracy   (on this PC)

After all categories finish, pushes the updated data/model files to the
EC2 server (reusing deploy/sync.sh) and restarts the remote Flask service
so the 30-minute in-memory cache doesn't delay fresh data reaching visitors.
EC2 itself never fetches, trains, or predicts -- it only serves what this
script pushes to it.
"""
import subprocess
import sys
import json
import logging
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"
PERFORMANCE_DIR = BASE_DIR / "performance"
LOGS_DIR.mkdir(exist_ok=True)
PERFORMANCE_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_DIR / 'automation.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

CATEGORIES = ['sports', 'crypto', 'stocks', 'real_estate', 'metals']


def run_category(category):
    """Run the full fetch -> retrain -> predict -> track cycle for one category."""
    from data_fetchers import fetch_category
    from model_trainer import ModelTrainingOrchestrator
    from performance_tracker import PerformanceOrchestrator

    logger.info(f"--- {category} ---")

    try:
        logger.info(f"[{category}] Fetching fresh data...")
        fetch_category(category)

        logger.info(f"[{category}] Retraining models...")
        ModelTrainingOrchestrator().train_one(category)

        logger.info(f"[{category}] Tracking prediction accuracy...")
        accuracy = PerformanceOrchestrator().track_one(category)

        logger.info(f"[{category}] Done. Accuracy: {accuracy}%")
        return True

    except Exception:
        logger.exception(f"[{category}] automation failed")
        return False


def deploy_to_server():
    """Push freshly trained data/models to EC2 and restart the remote Flask
    service, so it serves the new results immediately instead of waiting
    out the 30-min in-memory cache TTL. Runs deploy/sync.sh, which is a
    Bash script -- invoked here via Git Bash since this runs on Windows."""
    try:
        bash = r"C:\Program Files\Git\bin\bash.exe"
        subprocess.run([bash, 'deploy/sync.sh', 'data', 'model'], cwd=BASE_DIR, check=True)
        logger.info("Pushed updated data/models to EC2 and restarted remote app.")
    except Exception:
        logger.exception("Failed to deploy results to EC2")


def main():
    logger.info("=" * 60)
    logger.info("DAILY AUTOMATION CYCLE START")
    logger.info("=" * 60)

    results = {cat: run_category(cat) for cat in CATEGORIES}

    if any(results.values()):
        deploy_to_server()

    with open(LOGS_DIR / 'last_run.json', 'w') as f:
        json.dump(results, f, indent=2)

    logger.info("=" * 60)
    logger.info(f"CYCLE COMPLETE: {results}")
    logger.info("=" * 60)

    if not all(results.values()):
        sys.exit(1)


if __name__ == '__main__':
    main()
