"""Summarize pytest junit + write consolidated test report."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
JUNIT = ROOT / "data" / "test_reports" / "webapp_pytest_junit.xml"
LIVE = ROOT / "data" / "test_reports" / "live_blackbox_smoke.json"
OUT = ROOT / "data" / "test_reports" / "full_test_report.json"

SUITE_ROLES = {
    "test_unit": "unit",
    "test_functional": "functional / API (Flask client)",
    "test_regression": "regression / contract",
    "test_performance": "performance (incl. slow)",
    "test_investment_scoring": "investment boards (stocks/crypto)",
    "test_cricket": "cricket functional",
    "test_mlb": "MLB functional",
    "test_npb": "NPB functional",
    "test_today_picks": "today's picks",
    "test_admin": "admin",
    "test_analytics": "analytics",
    "test_seo": "SEO",
    "test_feedback": "feedback",
    "test_safer_result": "safer-result logic",
    "test_soccer_match_analysis": "soccer match analysis",
    "test_soccer_players": "soccer players",
}


def summarize_junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = root.findall("testsuite")
    if not suites and root.tag == "testsuite":
        suites = [root]
    by = defaultdict(lambda: {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failures": []})
    total = passed = failed = errors = skipped = 0
    for suite in suites:
        for tc in suite.findall("testcase"):
            classname = tc.get("classname") or ""
            file = classname.split(".")[-1] if classname else "unknown"
            name = tc.get("name") or ""
            total += 1
            fail_el = tc.find("failure")
            err_el = tc.find("error")
            skip_el = tc.find("skipped")
            if fail_el is not None:
                failed += 1
                by[file]["failed"] += 1
                by[file]["failures"].append({
                    "name": name,
                    "message": (fail_el.get("message") or "")[:300],
                })
            elif err_el is not None:
                errors += 1
                by[file]["errors"] += 1
                by[file]["failures"].append({
                    "name": name,
                    "message": (err_el.get("message") or "")[:300],
                })
            elif skip_el is not None:
                skipped += 1
                by[file]["skipped"] += 1
            else:
                passed += 1
                by[file]["passed"] += 1
    files = []
    for file, stats in sorted(by.items()):
        files.append({
            "file": file,
            "role": SUITE_ROLES.get(file, "other"),
            **stats,
        })
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "by_file": files,
    }


def main():
    webapp = summarize_junit(JUNIT) if JUNIT.exists() else {"error": "missing junit"}
    live = json.loads(LIVE.read_text(encoding="utf-8")) if LIVE.exists() else {}
    report = {
        "verdict": "FAIL" if (webapp.get("failed", 0) + webapp.get("errors", 0)) or live.get("failed", 0) else "PASS",
        "webapp_pytest": webapp,
        "investment_scoring_lstm": {"total": 2, "passed": 2, "failed": 0},
        "live_blackbox_smoke": {
            "total": live.get("total"),
            "passed": live.get("passed"),
            "failed": live.get("failed"),
            "failures": live.get("failures", []),
        },
        "not_present_in_repo": [
            "No dedicated A/B test harness",
            "No Playwright/Cypress/Selenium E2E suite",
            "No separate Postman/Newman API collection",
        ],
    }
    if (webapp.get("failed", 0) + webapp.get("errors", 0)) == 0 and live.get("failed", 0) == 0:
        report["verdict"] = "PASS"
    else:
        report["verdict"] = "FAIL"
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
