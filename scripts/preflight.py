"""Reset demo history and check scenarios, sample files, API key, and cache."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db import (  # noqa: E402
    DEFAULT_DB_PATH,
    get_existing_vendors,
    list_runs,
    reset_demo_data,
)
from app.main import DEMO_SCENARIOS  # noqa: E402
from engine.config import load_config  # noqa: E402
from engine.extract import FIELDS  # noqa: E402
from engine.llm import _cache_path  # noqa: E402
from engine.models import VendorSubmission  # noqa: E402
from engine.validation import run_pipeline  # noqa: E402


EXPECTED_STATUSES = {
    "happy.json": "APPROVED",
    "name_suffix_match.json": "APPROVED",
    "bank_account_collision.json": "REJECTED",
    "resubmission_missing_bank_letter.json": "PENDING",
    "resubmission_bank_letter_provided.json": "APPROVED",
    "indian_vendor_us_ein.json": "PENDING",
    "missing_bank_letter.json": "PENDING",
}


def read_sample(filename: str) -> dict:
    path = PROJECT_ROOT / "data" / "samples" / filename
    return json.loads(path.read_text(encoding="utf-8"))


def referenced_documents(payload: dict) -> list[tuple[str, str]]:
    """Return all path-bearing fields that stage 2 would ask Gemini to read."""
    documents = [
        (str(document_key), str(file_path))
        for document_key, file_path in payload.get("document_files", {}).items()
    ]
    scanned_path = payload.get("scanned_document_file")
    if scanned_path:
        documents.append(("scanned_document", str(scanned_path)))
    return documents


def source_file(raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def extraction_prompt(document_key: str) -> str:
    return (
        "Read this vendor onboarding document. Return exactly the requested JSON fields. "
        "Use an empty string for fields that are not present. Do not infer missing identifiers. "
        f"The only allowed keys are: {', '.join(FIELDS)}. "
        f"The expected document category is '{document_key}'."
    )


def extraction_is_cached(
    document_path: Path, document_key: str, ai_config: dict
) -> bool:
    """Check current and compatibility model keys without making an API call."""
    try:
        document_bytes = document_path.read_bytes()
    except OSError:
        return False
    model_names = [ai_config.get("model_name", "gemini-2.5-flash")]
    model_names.extend(ai_config.get("cache_compatibility_models", []))
    prompt = extraction_prompt(document_key)
    return any(
        _cache_path(document_bytes, prompt, str(model_name), ai_config).is_file()
        for model_name in model_names
    )


def gemini_key_is_set() -> bool:
    """Load local key sources but only return whether a non-empty key exists."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    if not os.getenv("GEMINI_API_KEY"):
        load_dotenv(PROJECT_ROOT.parent / ".env", override=False)
    if os.getenv("GEMINI_API_KEY"):
        return True

    # Streamlit Community Cloud secrets are TOML; inspect presence without
    # ever displaying or logging the secret value.
    secrets_path = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    try:
        secrets = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return bool(secrets.get("GEMINI_API_KEY"))


def check_demo_file_references(blockers: list[str]) -> None:
    print("\nDemo document files")
    print(f"{'Scenario':42} {'Document':32} Result")
    print("-" * 91)
    for label, sample_name in DEMO_SCENARIOS:
        try:
            payload = read_sample(sample_name)
            documents = referenced_documents(payload)
        except (OSError, json.JSONDecodeError) as error:
            print(f"{label:42} {'sample JSON':32} FAIL ({error})")
            blockers.append(f"repair data/samples/{sample_name}")
            continue

        for document_key, raw_path in documents:
            exists = source_file(raw_path).is_file()
            print(f"{label:42} {document_key:32} {'PASS' if exists else 'FAIL'}")
            if not exists:
                blockers.append(f"restore {raw_path} referenced by {sample_name}")


def check_pipeline_statuses(blockers: list[str]) -> None:
    print("\nDeterministic pipeline scenarios")
    print(f"{'Scenario':42} {'Expected':12} {'Actual':12} Result")
    print("-" * 78)

    # Use an isolated database so the preflight leaves the real demo database
    # clean after the requested reseed.
    data_dir = PROJECT_ROOT / "data"
    with tempfile.TemporaryDirectory(
        prefix="preflight-", dir=data_dir
    ) as temporary_directory:
        check_db_path = Path(temporary_directory) / "preflight.sqlite3"
        reset_demo_data(check_db_path)
        for label, sample_name in DEMO_SCENARIOS:
            expected = EXPECTED_STATUSES.get(sample_name, "UNCONFIGURED")
            try:
                submission = VendorSubmission(**read_sample(sample_name))
                decision, _ = run_pipeline(submission, db_path=check_db_path)
                actual = decision.status
                passed = expected == actual
                result_label = "PASS" if passed else "FAIL"
                print(f"{label:42} {expected:12} {actual:12} {result_label}")
                if not passed:
                    blockers.append(
                        f"{sample_name} expected {expected}, got {actual}"
                    )
            except Exception as error:
                print(f"{label:42} {expected:12} {'ERROR':12} FAIL ({error})")
                blockers.append(f"fix pipeline scenario {sample_name}: {error}")


def check_sample_document_caches(
    key_available: bool, blockers: list[str]
) -> None:
    config = load_config()
    ai_config = config.get("ai", {})
    missing: list[tuple[str, str]] = []
    checked = 0

    print("\nSample document extraction cache")
    print(f"{'Sample':40} {'Document':32} Result")
    print("-" * 84)
    for sample_path in sorted((PROJECT_ROOT / "data" / "samples").glob("*.json")):
        try:
            payload = json.loads(sample_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            blockers.append(f"repair {sample_path.relative_to(PROJECT_ROOT)}: {error}")
            continue
        for document_key, raw_path in referenced_documents(payload):
            checked += 1
            file_path = source_file(raw_path)
            cached = file_path.is_file() and extraction_is_cached(
                file_path, document_key, ai_config
            )
            label = "CACHED" if cached else "NOT CACHED"
            print(f"{sample_path.name:40} {document_key:32} {label}")
            if not cached:
                missing.append((sample_path.name, document_key))

    print(f"Checked {checked} referenced sample document(s).")
    if missing:
        if key_available:
            blockers.append("run scripts/warm_cache.py to cache every sample document")
        else:
            blockers.append(
                "set GEMINI_API_KEY, then run scripts/warm_cache.py to cache every sample document"
            )
        print("Cache warm-up needed: run scripts/warm_cache.py before recording.")


def main() -> int:
    blockers: list[str] = []
    print("Vendor Onboarding recording preflight")

    database_existed = DEFAULT_DB_PATH.is_file()
    print(f"Database existed before preflight: {'yes' if database_existed else 'no'}")
    try:
        reset_demo_data(DEFAULT_DB_PATH)
        vendors = get_existing_vendors(DEFAULT_DB_PATH)
        runs = list_runs(DEFAULT_DB_PATH, limit=None)
        database_ready = DEFAULT_DB_PATH.is_file() and bool(vendors) and not runs
        print(
            f"{'PASS' if database_ready else 'FAIL'}: Demo database "
            f"{'reseeded with ' + str(len(vendors)) + ' vendors and no saved runs' if database_ready else 'could not be reseeded cleanly'}."
        )
        if not database_ready:
            blockers.append("reseed data/vendor_onboarding.sqlite3 cleanly")
    except Exception as error:
        print(f"FAIL: Could not reseed the demo database ({error}).")
        blockers.append(f"fix the demo database at {DEFAULT_DB_PATH}")

    check_demo_file_references(blockers)
    check_pipeline_statuses(blockers)

    key_available = gemini_key_is_set()
    print(
        f"\nGEMINI_API_KEY available: {'yes' if key_available else 'no'} "
        "(value hidden)"
    )
    check_sample_document_caches(key_available, blockers)

    if blockers:
        # Preserve order while removing duplicate remediation items.
        unique_blockers = list(dict.fromkeys(blockers))
        print("\nNOT READY TO RECORD: " + "; ".join(unique_blockers))
        return 1
    print("\nREADY TO RECORD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
