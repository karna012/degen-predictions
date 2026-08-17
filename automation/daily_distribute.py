#!/usr/bin/env python3
"""Thin alias for the daily digest job (implementation lives in webapp/).

  python automation/daily_distribute.py --dry-run
  python automation/daily_distribute.py --force-repost
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEBAPP = REPO / "webapp"
sys.path.insert(0, str(WEBAPP))
os.chdir(WEBAPP)

import daily_digest  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(daily_digest.main())
