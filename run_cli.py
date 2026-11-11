"""Run the deterministic validation pipeline for a JSON vendor submission."""

import argparse
import json
from pathlib import Path

from engine.models import VendorSubmission
from engine.validation import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a vendor submission JSON file.")
    parser.add_argument("submission", type=Path, help="Path to the submission JSON")
    args = parser.parse_args()

    with args.submission.open("r", encoding="utf-8") as file:
        submission = VendorSubmission(**json.load(file))
    decision, results = run_pipeline(submission)
    print(f"Run ID: {decision.run_id}")
    print(f"Decision: {decision.status}")
    for reason in decision.reasons:
        print(f"- {reason}")
    print("\nCheck results:")
    for result in results:
        print(f"[{result.status.upper()}] {result.stage} / {result.check_name}: {result.reason}")


if __name__ == "__main__":
    main()
