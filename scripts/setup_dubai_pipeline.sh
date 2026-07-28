#!/bin/bash
# Complete setup for Dubai real estate predictions
# Collects 12 years of historical data and trains models

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCRIPTS_DIR="$REPO_ROOT/scripts"

echo "================================================"
echo "DUBAI REAL ESTATE PIPELINE SETUP"
echo "================================================"

cd "$REPO_ROOT"

# Step 1: Collect historical data
echo ""
echo "[1/3] Collecting 12+ years of historical Dubai property data..."
python "$SCRIPTS_DIR/collect_dubai_historical_data.py"

# Step 2: Train models
echo ""
echo "[2/3] Training ensemble models on historical data..."
python "$SCRIPTS_DIR/train_dubai_models.py"

# Step 3: Verify models
echo ""
echo "[3/3] Verifying model files..."
if [ -f "model/dubai/appreciation_model.joblib" ]; then
    echo "✓ Appreciation model trained"
fi
if [ -f "model/dubai/rental_yield_model.joblib" ]; then
    echo "✓ Rental yield model trained"
fi

echo ""
echo "================================================"
echo "✅ SETUP COMPLETE"
echo "================================================"
echo ""
echo "Dubai real estate predictions are ready."
echo "Access at: https://degenpredictions.com/dubai"
echo ""
echo "Models will be automatically retrained weekly"
echo "with new market data."
