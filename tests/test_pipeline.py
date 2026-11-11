import json
from pathlib import Path

import pytest

from app.db import create_run, get_linked_runs, get_run, get_run_events, record_event
from engine.resubmission import describe_resubmission_changes
from engine.models import CheckResult, VendorSubmission
from engine.validation import normalize_company_name, run_pipeline


SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def load_submission(filename: str) -> VendorSubmission:
    with (SAMPLES / filename).open("r", encoding="utf-8") as file:
        return VendorSubmission(**json.load(file))


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("happy.json", "APPROVED"),
        ("missing_bank_letter.json", "PENDING"),
        ("blocked_country.json", "REJECTED"),
        ("indian_vendor_us_ein.json", "PENDING"),
        ("country_multiple_conflicts.json", "REJECTED"),
        ("name_suffix_match.json", "APPROVED"),
        ("name_real_mismatch.json", "PENDING"),
    ],
)
def test_sample_submission_status(filename: str, expected: str, tmp_path: Path) -> None:
    decision, results = run_pipeline(
        load_submission(filename), vendors=[], db_path=tmp_path / "runs.sqlite3"
    )

    assert decision.status == expected
    assert decision.run_id
    assert results[-1].stage == "Decision"


def test_stage_two_warning_is_included_in_decision(tmp_path: Path) -> None:
    prior_warning = CheckResult(
        stage="Document reading",
        check_name="document_tax_certificate",
        status="warn",
        reason="Document extraction was skipped; submitted form values were retained.",
    )

    decision, results = run_pipeline(
        load_submission("happy.json"),
        vendors=[],
        db_path=tmp_path / "runs.sqlite3",
        prior_results=[prior_warning],
    )

    assert decision.status == "PENDING"
    assert any(result is prior_warning for result in results)


def test_unsupported_country_returns_explainable_checks(tmp_path: Path) -> None:
    submission = VendorSubmission(
        company_name="Example Co",
        country="Mars",
        bank_account_holder_name="Example Co",
        tax_certificate_name="Example Co",
    )
    decision, results = run_pipeline(
        submission, vendors=[], db_path=tmp_path / "runs.sqlite3"
    )
    country_check = next(result for result in results if result.check_name == "supported_country")

    assert decision.status == "PENDING"
    assert country_check.status == "fail"
    assert "Mars" in country_check.reason
    assert "not a supported country" in country_check.reason


def test_single_foreign_format_conflict_is_pending_and_specific(tmp_path: Path) -> None:
    decision, results = run_pipeline(
        load_submission("indian_vendor_us_ein.json"),
        vendors=[],
        db_path=tmp_path / "runs.sqlite3",
    )
    conflict = next(result for result in results if result.check_name == "country_evidence_tax_id")

    assert decision.status == "PENDING"
    assert "US" in conflict.reason
    assert "EIN" in conflict.reason
    assert "India" in conflict.reason
    assert "GSTIN" in conflict.reason


def test_multiple_foreign_format_conflicts_are_rejected_with_field_reasons(tmp_path: Path) -> None:
    decision, results = run_pipeline(
        load_submission("country_multiple_conflicts.json"),
        vendors=[],
        db_path=tmp_path / "runs.sqlite3",
    )
    conflicts = [result for result in results if result.check_name.startswith("country_evidence_")]

    assert decision.status == "REJECTED"
    assert any("tax id" in result.reason.casefold() and "EIN" in result.reason for result in conflicts)
    assert any("bank code" in result.reason.casefold() and "sort code" in result.reason.casefold() for result in conflicts)


def test_name_match_audit_explains_suffix_and_word_order_normalization(tmp_path: Path) -> None:
    decision, results = run_pipeline(
        load_submission("name_suffix_match.json"),
        vendors=[],
        db_path=tmp_path / "runs.sqlite3",
    )
    bank_match = next(result for result in results if result.check_name == "name_match_bank_account_holder")

    assert decision.status == "APPROVED"
    assert bank_match.status == "pass"
    assert "SUNRISE TEXTILES PRIVATE LIMITED' became 'sunrise textiles'" in bank_match.reason
    assert "Pvt Ltd" in bank_match.reason
    assert "compared sorted words" in bank_match.reason.casefold()


def test_name_normalization_handles_punctuation_and_reordered_words() -> None:
    assert normalize_company_name("SUNRISE TEXTILES PRIVATE LIMITED") == normalize_company_name(
        "Textiles, Sunrise Pvt. Ltd."
    )


def test_real_name_mismatch_is_not_normalized_away(tmp_path: Path) -> None:
    decision, results = run_pipeline(
        load_submission("name_real_mismatch.json"),
        vendors=[],
        db_path=tmp_path / "runs.sqlite3",
    )
    bank_match = next(result for result in results if result.check_name == "name_match_bank_account_holder")

    assert decision.status == "PENDING"
    assert bank_match.status == "fail"
    assert "sunrise trading" in bank_match.reason
    assert "sunrise textiles" in bank_match.reason
    assert "Similarity is" in bank_match.reason


def _existing_vendors() -> list[dict[str, str]]:
    path = Path(__file__).resolve().parents[1] / "data" / "existing_vendors.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_existing_account_owned_by_another_vendor_rejects_and_names_owner(tmp_path: Path) -> None:
    decision, results = run_pipeline(
        load_submission("bank_account_collision.json"),
        vendors=_existing_vendors(),
        db_path=tmp_path / "runs.sqlite3",
    )
    collision = next(result for result in results if result.check_name == "bank_account_reuse")

    assert decision.status == "REJECTED"
    assert collision.status == "fail"
    assert "Aarav Industrial Supplies Pvt Ltd" in collision.reason
    assert "SAMPLE-001" in collision.reason


def test_same_vendor_resubmission_is_not_a_bank_account_collision(tmp_path: Path) -> None:
    decision, results = run_pipeline(
        load_submission("bank_account_same_vendor.json"),
        vendors=_existing_vendors(),
        db_path=tmp_path / "runs.sqlite3",
    )
    collision = next(result for result in results if result.check_name == "bank_account_reuse")

    assert collision.status == "pass"
    assert "not recorded for a different vendor" in collision.reason
    # The pre-existing duplicate-tax-ID policy may still leave the resubmission
    # pending review, but it must not be rejected as account sharing.
    assert decision.status != "REJECTED"


def test_bank_account_used_in_a_previous_run_is_reported_with_other_checks(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite3"
    first_submission = load_submission("happy.json")
    first_decision, _ = run_pipeline(first_submission, vendors=[], db_path=database)
    assert first_decision.status == "APPROVED"

    payload = dict(first_submission.__dict__)
    payload.update(
        company_name="Harbor Components Pvt Ltd",
        tax_id="29ABCDE1234F1Z5",
        bank_account_holder_name="Harbor Components Pvt Ltd",
        tax_certificate_name="Harbor Components Pvt Ltd",
    )
    second_submission = VendorSubmission(**payload)
    document_mismatch = CheckResult(
        stage="Document reading",
        check_name="document_bank_letter",
        status="warn",
        reason="Bank account number in the bank letter does not match the submitted account number.",
    )
    decision, results = run_pipeline(
        second_submission,
        vendors=[],
        db_path=database,
        prior_results=[document_mismatch],
    )

    collision = next(result for result in results if result.check_name == "bank_account_reuse")
    assert decision.status == "REJECTED"
    assert collision.status == "fail"
    assert first_decision.run_id in collision.reason
    assert document_mismatch.reason in decision.reasons


def test_pending_vendor_resubmission_links_runs_and_records_new_document(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite3"
    first_submission = load_submission("resubmission_missing_bank_letter.json")
    first_decision, _ = run_pipeline(first_submission, vendors=[], db_path=database)
    assert first_decision.status == "PENDING"

    next_submission = load_submission("resubmission_bank_letter_provided.json")
    second_run_id = create_run(next_submission, database)
    second_run = get_run(second_run_id, database)
    previous_run_id = second_run["linked_previous_run_id"]
    previous_run = get_run(previous_run_id, database)
    changes = describe_resubmission_changes(previous_run, next_submission)

    assert previous_run_id == first_decision.run_id
    assert changes is not None
    assert changes.status == "pass"
    assert "document: bank_letter" in changes.reason
    record_event(second_run_id, changes, database)

    second_decision, _ = run_pipeline(
        next_submission,
        vendors=[],
        db_path=database,
        run_id=second_run_id,
    )

    assert second_decision.status == "APPROVED"
    assert get_run(second_run_id, database)["linked_previous_run_id"] == first_decision.run_id
    assert any(
        event["check_name"] == "resubmission_changes"
        and "document: bank_letter" in event["reason"]
        for event in get_run_events(second_run_id, database)
    )
    linked_runs = get_linked_runs(second_run_id, database)
    assert [run["id"] for run in linked_runs] == [first_decision.run_id, second_run_id]
    assert [run["status"] for run in linked_runs] == ["PENDING", "APPROVED"]
