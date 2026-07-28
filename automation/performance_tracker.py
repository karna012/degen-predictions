"""
Performance tracking for all prediction categories.
Compares predictions against actual outcomes once they're known.

NOTE: Scaffolding -- reads from data/<category>/predictions.csv and
actuals files that need to exist for real numbers to come out; until
those pipelines produce them, this logs 0 tracked predictions and moves on.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
PERFORMANCE_DIR = BASE_DIR / "performance"
PERFORMANCE_DIR.mkdir(exist_ok=True)


class PerformanceTracker:
    def __init__(self, category, predictions_file, actuals_file, key_col):
        self.category = category
        self.predictions_file = DATA_DIR / predictions_file
        self.actuals_file = DATA_DIR / actuals_file
        self.key_col = key_col
        self.stats_file = PERFORMANCE_DIR / f"{category}_stats.json"

    def _score(self, predicted_row, actual_row):
        raise NotImplementedError

    def track(self):
        if not self.predictions_file.exists() or not self.actuals_file.exists():
            logger.info(f"[{self.category}] No predictions/actuals to compare yet.")
            return 0.0

        predictions = pd.read_csv(self.predictions_file)
        actuals = pd.read_csv(self.actuals_file)

        scores = []
        for _, pred in predictions.iterrows():
            match = actuals[actuals[self.key_col] == pred[self.key_col]]
            if not match.empty:
                scores.append(self._score(pred, match.iloc[0]))

        accuracy = round((sum(scores) / len(scores)) * 100, 2) if scores else 0.0

        with open(self.stats_file, 'w') as f:
            json.dump({
                'category': self.category,
                'accuracy_percent': accuracy,
                'predictions_compared': len(scores),
                'timestamp': datetime.now().isoformat(),
            }, f, indent=2)

        logger.info(f"[{self.category}] accuracy: {accuracy}% over {len(scores)} predictions")
        return accuracy


class SportsPerformanceTracker(PerformanceTracker):
    def __init__(self):
        super().__init__('sports', 'sports/predictions.csv', 'sports/results.csv', 'match_id')

    def _score(self, pred, actual):
        return 1.0 if pred.get('predicted_result') == actual.get('actual_result') else 0.0


class CryptoPerformanceTracker(PerformanceTracker):
    def __init__(self):
        super().__init__('crypto', 'crypto/predictions.csv', 'crypto/returns_6m.csv', 'asset')

    def _score(self, pred, actual):
        error = abs(pred.get('predicted_return', 0) - actual.get('actual_return', 0))
        return max(0.0, 1 - error / max(abs(actual.get('actual_return', 1)), 1))


class StocksPerformanceTracker(PerformanceTracker):
    def __init__(self):
        super().__init__('stocks', 'stocks/predictions.csv', 'stocks/actual_returns.csv', 'symbol')

    def _score(self, pred, actual):
        error = abs(pred.get('predicted_return', 0) - actual.get('actual_return', 0))
        return max(0.0, 1 - error / max(abs(pred.get('predicted_return', 1)), 1))


class RealEstatePerformanceTracker(PerformanceTracker):
    def __init__(self):
        super().__init__('real_estate', 'dubai/predictions.csv', 'dubai/actual_outcomes.csv', 'property_id')

    def _score(self, pred, actual):
        error = abs(pred.get('predicted_roi_percent', 0) - actual.get('actual_roi_percent', 0))
        return max(0.0, 1 - error / 100)


class MetalsPerformanceTracker(PerformanceTracker):
    def __init__(self):
        super().__init__('metals', 'metals/predictions.csv', 'metals/actual_prices.csv', 'metal')

    def _score(self, pred, actual):
        actual_price = actual.get('actual_price', 1)
        error = abs(pred.get('predicted_price', 1) - actual_price) / actual_price
        return max(0.0, 1 - error)


TRACKERS = {
    'sports': SportsPerformanceTracker,
    'crypto': CryptoPerformanceTracker,
    'stocks': StocksPerformanceTracker,
    'real_estate': RealEstatePerformanceTracker,
    'metals': MetalsPerformanceTracker,
}


class PerformanceOrchestrator:
    def track_one(self, category):
        tracker_cls = TRACKERS.get(category)
        if not tracker_cls:
            raise ValueError(f"Unknown category: {category}")
        return tracker_cls().track()

    def track_all(self):
        return {cat: self.track_one(cat) for cat in TRACKERS}


if __name__ == '__main__':
    print(json.dumps(PerformanceOrchestrator().track_all(), indent=2))
