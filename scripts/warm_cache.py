"""Run document extraction for every sample to prefill the local Gemini cache."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")
if not os.getenv("GEMINI_API_KEY"):
    load_dotenv(PROJECT_ROOT.parent / ".env")

from engine.extract import read_documents
from engine.models import VendorSubmission


def main() -> None:
    samples = sorted((PROJECT_ROOT / "data" / "samples").glob("*.json"))
    total_docs = cached_docs = fresh_docs = warnings = 0
    for sample_path in samples:
        payload = json.loads(sample_path.read_text(encoding="utf-8"))
        submission = VendorSubmission(**payload)
        result = read_documents(submission)
        for document in result.documents:
            total_docs += 1
            if document.from_cache:
                cached_docs += 1
            elif document.extracted:
                fresh_docs += 1
        warnings += sum(check.status == "warn" for check in result.checks)
        print(f"{sample_path.name}: processed {len(result.documents)} document(s)")

    print(
        f"Cache warm-up finished: {total_docs} referenced document(s), "
        f"{fresh_docs} fresh, {cached_docs} cached, {warnings} warning(s)."
    )
    if warnings:
        print("Review document paths and GEMINI_API_KEY if documents could not be read.")


if __name__ == "__main__":
    main()
