"""Deterministic validation stages 3 through 7."""

import re
from pathlib import Path
from typing import Any, Callable, Iterable

from rapidfuzz.fuzz import ratio

from app.db import (
    DEFAULT_DB_PATH,
    complete_run,
    create_run,
    get_existing_vendors,
    get_historical_vendor_records,
    get_run,
    get_run_events,
    record_event,
)
from engine.config import load_config
from engine.document_utils import provided_document_names
from engine.models import CheckResult, PipelineDecision, VendorSubmission
from engine.resubmission import describe_resubmission_changes


def _result(stage: str, name: str, status: str, reason: str) -> CheckResult:
    return CheckResult(stage=stage, check_name=name, status=status, reason=reason)  # type: ignore[arg-type]


def _get_value(submission: VendorSubmission, key: str) -> Any:
    """Read normal model fields first, then extra intake fields."""
    if hasattr(submission, key):
        return getattr(submission, key)
    return submission.additional_data.get(key)


def stage_3_completeness(
    submission: VendorSubmission, config: dict[str, Any] | None = None
) -> list[CheckResult]:
    """Check configured fields and documents for the claimed country."""
    rules = config or load_config()
    country = submission.country
    required_fields = rules.get("required_fields", {}).get(country)
    required_documents = rules.get("required_documents", {}).get(country)
    results: list[CheckResult] = []

    if required_fields is None or required_documents is None:
        return [_result("Completeness", "country_requirements", "warn",
                        f"No completeness requirements are configured for {country!r}.")]

    missing_fields = [field for field in required_fields if not _get_value(submission, field)]
    results.append(_result(
        "Completeness", "required_fields", "fail" if missing_fields else "pass",
        (f"Missing required fields: {', '.join(missing_fields)}." if missing_fields
         else f"All {len(required_fields)} required fields are present for {country}.")
    ))

    submitted_documents = provided_document_names(submission.document_files)
    missing_documents = [doc for doc in required_documents if doc.casefold() not in submitted_documents]
    results.append(_result(
        "Completeness", "required_documents", "fail" if missing_documents else "pass",
        (f"Missing required documents: {', '.join(missing_documents)}." if missing_documents
         else f"All {len(required_documents)} required documents are present for {country}.")
    ))
    return results


def stage_4_format_validation(
    submission: VendorSubmission, config: dict[str, Any] | None = None
) -> list[CheckResult]:
    """Validate supported country, tax ID, bank code, and email syntax."""
    rules = config or load_config()
    country = submission.country
    results: list[CheckResult] = []
    supported = country in rules.get("tax_id_formats", {}) and country in rules.get("bank_code_formats", {})
    results.append(_result("Format validation", "supported_country", "pass" if supported else "fail",
                           f"{country!r} is a supported country." if supported
                           else f"{country!r} is not a supported country in the rules configuration."))

    tax_rule = rules.get("tax_id_formats", {}).get(country)
    tax_ok = bool(tax_rule and re.fullmatch(tax_rule["regex"], submission.tax_id or ""))
    tax_name = tax_rule.get("name", "tax ID") if tax_rule else "tax ID"
    results.append(_result("Format validation", "tax_id_format", "pass" if tax_ok else "fail",
                           f"{tax_name} matches the configured format for {country}." if tax_ok
                           else f"Tax ID {submission.tax_id!r} does not match the configured {tax_name} format for {country}."))

    bank_rule = rules.get("bank_code_formats", {}).get(country)
    if bank_rule and "any_of" in bank_rule:
        matching = [item["name"] for item in bank_rule["any_of"]
                    if re.fullmatch(item["regex"], submission.bank_code or "")]
        bank_ok = bool(matching)
        bank_format = ", ".join(matching) if matching else "configured UK sort code or SWIFT formats"
    elif bank_rule:
        bank_ok = bool(re.fullmatch(bank_rule["regex"], submission.bank_code or ""))
        bank_format = bank_rule.get("name", "bank code")
    else:
        bank_ok = False
        bank_format = "configured bank code"
    results.append(_result("Format validation", "bank_code_format", "pass" if bank_ok else "fail",
                           f"Bank code matches the {bank_format} format." if bank_ok
                           else f"Bank code {submission.bank_code!r} does not match the {bank_format} format for {country}."))

    email = submission.contact_email.strip()
    email_ok = bool(re.fullmatch(rules["email_format"]["regex"], email))
    results.append(_result("Format validation", "email_format", "pass" if email_ok else "fail",
                           f"Contact email {email!r} has a valid basic email format." if email_ok
                           else f"Contact email {email!r} is missing or does not look valid."))

    # Call out evidence that is valid for a different supported country. A
    # single field may be a typo; several conflicting fields are stronger evidence.
    for other_country, other_rule in rules.get("tax_id_formats", {}).items():
        if other_country == country or not submission.tax_id:
            continue
        if re.fullmatch(other_rule["regex"], submission.tax_id):
            expected = tax_rule.get("name", "tax ID") if tax_rule else "a country-specific tax ID"
            results.append(_result(
                "Format validation", "country_evidence_tax_id", "warn",
                f"Tax ID {submission.tax_id!r} matches the {other_rule.get('name', 'tax ID')} format for {other_country}, "
                f"but the form claims {country}, which expects {expected}. Correct the tax ID or claimed country.",
            ))
            break

    for other_country, other_rule in rules.get("bank_code_formats", {}).items():
        if other_country == country or not submission.bank_code:
            continue
        candidates = other_rule.get("any_of", [other_rule])
        matching = [str(item.get("name", "bank code")) for item in candidates
                    if re.fullmatch(item["regex"], submission.bank_code)]
        if matching:
            expected = bank_rule.get("name", "bank code") if bank_rule else "a country-specific bank code"
            results.append(_result(
                "Format validation", "country_evidence_bank_code", "warn",
                f"Bank code {submission.bank_code!r} matches the {', '.join(matching).replace('_', ' ')} format for {other_country}, "
                f"but the form claims {country}, which expects {expected}. Correct the bank code or claimed country.",
            ))
            break

    if submission.bank_country.strip() and submission.bank_country.strip().casefold() != country.strip().casefold():
        results.append(_result(
            "Format validation", "country_evidence_bank_country", "warn",
            f"Bank country {submission.bank_country!r} conflicts with claimed country {country!r}. "
            f"The bank country should be {country!r} to match the form, or correct the claimed country.",
        ))
    return results


def _name_tokens(name: str) -> list[str]:
    """Case-fold and replace punctuation with spaces, preserving word boundaries."""
    folded = name.casefold()
    normalized_chars = [char if char.isalnum() or char.isspace() else " " for char in folded]
    return "".join(normalized_chars).split()


def _normalize_company_name_details(
    name: str, config: dict[str, Any]
) -> tuple[str, list[str]]:
    """Return an order-independent canonical name and configured suffixes removed."""
    tokens = _name_tokens(name)
    suffix_variants = [
        (str(value), _name_tokens(str(value)))
        for value in config.get("company_name_suffixes", {}).get("variants", [])
    ]
    suffix_variants = [(label, parts) for label, parts in suffix_variants if parts]
    # Prefer the longest suffix phrase, e.g. "private limited" before "limited".
    suffix_variants.sort(key=lambda item: len(item[1]), reverse=True)
    removed: list[str] = []
    while tokens:
        matching = next(
            (item for item in suffix_variants if len(tokens) >= len(item[1])
             and tokens[-len(item[1]):] == item[1]),
            None,
        )
        if not matching:
            break
        label, parts = matching
        del tokens[-len(parts):]
        removed.append(label)
    # Sorting makes reordered words compare equally without dropping any words.
    return " ".join(sorted(tokens)), removed


def normalize_company_name(name: str, config: dict[str, Any] | None = None) -> str:
    """Canonicalize case, punctuation, legal suffixes, and word order."""
    rules = config or load_config()
    normalized, _ = _normalize_company_name_details(name, rules)
    return normalized


def stage_5_cross_referencing(
    submission: VendorSubmission, config: dict[str, Any] | None = None
) -> list[CheckResult]:
    """Compare company names and confirm the bank country matches the claim."""
    rules = config or load_config()
    form_name, form_suffixes = _normalize_company_name_details(submission.company_name, rules)
    threshold = rules.get("name_similarity", {})
    approve_at = float(threshold["approval_threshold"])
    pending_at = float(threshold["pending_threshold"])
    results: list[CheckResult] = []
    comparison_names = [
        ("bank account holder", submission.bank_account_holder_name),
        ("tax certificate", submission.tax_certificate_name),
    ]
    for source, source_name in comparison_names:
        normalized, removed_suffixes = _normalize_company_name_details(source_name, rules)
        score = ratio(form_name, normalized) if form_name and normalized else 0.0
        suffix_note = (
            f"Removed configured legal suffixes from form: {', '.join(form_suffixes) or 'none'}; "
            f"from {source}: {', '.join(removed_suffixes) or 'none'}."
        )
        normalization_note = (
            f"Normalization applied: case-folded and changed punctuation to spaces; "
            f"form '{submission.company_name}' became '{form_name}', and {source} "
            f"'{source_name}' became '{normalized}'. Compared sorted words so word order does not affect the score. "
            f"{suffix_note}"
        )
        if score >= approve_at:
            status = "pass"
            reason = f"{normalization_note} Names match with similarity {score:.1f} (approval threshold {approve_at:g})."
        elif score >= pending_at:
            status = "warn"
            reason = f"{normalization_note} Similarity is {score:.1f}; approval requires {approve_at:g}."
        else:
            status = "fail"
            reason = f"{normalization_note} Similarity is {score:.1f}, below pending threshold {pending_at:g}."
        results.append(_result("Cross-referencing", f"name_match_{source.replace(' ', '_')}", status, reason))

    bank_country = submission.bank_country.strip()
    country_matches = bool(bank_country) and bank_country.casefold() == submission.country.strip().casefold()
    results.append(_result("Cross-referencing", "bank_country_match",
                           "pass" if country_matches else "fail",
                           f"Bank country {bank_country!r} matches claimed country {submission.country!r}." if country_matches
                           else f"Bank country {bank_country!r} does not match claimed country {submission.country!r}."))
    return results


def stage_6_risk_checks(
    submission: VendorSubmission,
    config: dict[str, Any] | None = None,
    vendors: Iterable[dict[str, Any]] | None = None,
    historical_vendors: Iterable[dict[str, Any]] | None = None,
) -> list[CheckResult]:
    """Check blocked country, duplicate tax ID, and reused bank account."""
    rules = config or load_config()
    known = list(vendors or [])
    blocked = {str(country).casefold() for country in rules.get("blocked_countries", [])}
    is_blocked = submission.country.casefold() in blocked
    results = [_result("Risk checks", "blocked_country", "fail" if is_blocked else "pass",
                       f"Country {submission.country!r} is on the blocked-country list." if is_blocked
                       else f"Country {submission.country!r} is not on the blocked-country list.")]

    tax_duplicates = [v for v in known if submission.tax_id and str(v.get("tax_id", "")).casefold() == submission.tax_id.casefold()]
    results.append(_result("Risk checks", "duplicate_tax_id", "warn" if tax_duplicates else "pass",
                           f"Tax ID is already recorded for vendor(s): {', '.join(str(v.get('vendor_id', v.get('company_name', 'unknown'))) for v in tax_duplicates)}."
                           if tax_duplicates else "Tax ID is not present in the existing vendor records."))

    bank_owners = known + list(historical_vendors or [])
    bank_matches = [
        vendor for vendor in bank_owners
        if submission.bank_account_number
        and str(vendor.get("bank_account_number", "")).strip() == submission.bank_account_number.strip()
    ]
    # A resubmission is the same vendor only when both stable identifiers agree.
    # A shared account under a different name remains a rejection even if a tax
    # identifier was copied into the new submission.
    normalized_submission_name = normalize_company_name(submission.company_name, rules)
    identity_fields = set(
        rules.get("bank_account_reuse", {}).get(
            "same_vendor_identity_fields", ["tax_id", "company_name"]
        )
    )
    bank_different = [
        vendor for vendor in bank_matches
        if not (
            ("tax_id" not in identity_fields or
             str(vendor.get("tax_id", "")).strip().casefold() == submission.tax_id.strip().casefold())
            and ("company_name" not in identity_fields or
                 normalize_company_name(str(vendor.get("company_name", "")), rules)
                 == normalized_submission_name)
        )
    ]
    collision_names = [
        f"{vendor.get('company_name', 'Unknown vendor')} ({vendor.get('vendor_id', 'unknown ID')})"
        for vendor in bank_different
    ]
    results.append(_result(
        "Risk checks",
        "bank_account_reuse",
        "fail" if bank_different else "pass",
        (
            f"Bank account number is already used by a different vendor: {', '.join(collision_names)}. "
            "This account ownership collision requires rejection."
            if bank_different
            else "Bank account number is not recorded for a different vendor; a matching account for the same named vendor and tax ID is allowed."
        ),
    ))
    return results


def stage_7_decision(results: list[CheckResult], config: dict[str, Any] | None = None) -> list[CheckResult]:
    """Apply the configured decision policy and summarize all relevant reasons."""
    # The policy has explicit rejection triggers. Other failures and warnings are pending.
    tax_invalid = any(r.check_name == "tax_id_format" and r.status == "fail" for r in results)
    name_mismatch = any(r.check_name.startswith("name_match_") and r.status == "fail" for r in results)
    blocked = any(r.check_name == "blocked_country" and r.status == "fail" for r in results)
    shared_bank = any(r.check_name == "bank_account_reuse" and r.status == "fail" for r in results)
    country_evidence_conflicts = [
        r for r in results
        if r.check_name in {
            "country_evidence_tax_id", "country_evidence_bank_code", "country_evidence_bank_country"
        } and r.status != "pass"
    ]
    conflicting_country_evidence = len(country_evidence_conflicts) > 1 or (
        len(country_evidence_conflicts) == 1 and name_mismatch
    )

    if blocked or shared_bank or (tax_invalid and name_mismatch) or conflicting_country_evidence:
        status = "REJECTED"
    elif any(r.status in {"fail", "warn"} for r in results):
        status = "PENDING"
    else:
        status = "APPROVED"

    reasons: list[str] = []
    for result in results:
        if result.status != "pass":
            reasons.append(result.reason)
    if status == "APPROVED":
        reasons.append("All configured checks passed.")
    elif status == "PENDING":
        reasons.append("Review or missing information is required before approval.")
    return [_result("Decision", "final_decision", "pass" if status == "APPROVED" else
                    "fail" if status == "REJECTED" else "warn",
                    f"{status}: " + " ".join(reasons))]


def run_pipeline(
    submission: VendorSubmission,
    config: dict[str, Any] | None = None,
    vendors: Iterable[dict[str, Any]] | None = None,
    event_callback: Callable[[str, CheckResult], None] | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    run_id: str | None = None,
    prior_results: Iterable[CheckResult] | None = None,
) -> tuple[PipelineDecision, list[CheckResult]]:
    """Run stages 3-7, persisting and emitting each result as it is produced.

    The optional callback receives the run ID and each persisted check result,
    allowing a future UI to update incrementally while the pipeline is running.
    """
    rules = config or load_config()
    run_id = run_id or create_run(submission, db_path)
    existing_vendors = (
        list(vendors) if vendors is not None else get_existing_vendors(db_path)
    )
    historical_vendors = get_historical_vendor_records(db_path, exclude_run_id=run_id)
    # Earlier stages (such as document reading) are already persisted by the
    # caller, but their warnings still participate in the final policy.
    earlier_results = list(prior_results or [])
    results: list[CheckResult] = []

    # CLI and other non-UI callers also get the resubmission audit event. The
    # Live Run page may already have written it, so avoid recording a duplicate.
    current_run = get_run(run_id, db_path)
    previous_id = current_run.get("linked_previous_run_id") if current_run else None
    previous_run = get_run(previous_id, db_path) if previous_id else None
    if previous_run:
        resubmission_result = describe_resubmission_changes(previous_run, submission, rules)
        if resubmission_result:
            existing_events = get_run_events(run_id, db_path)
            already_recorded = any(
                event["check_name"] == resubmission_result.check_name
                for event in existing_events
            )
            results.append(resubmission_result)
            if not already_recorded:
                record_event(run_id, resubmission_result, db_path)
                if event_callback is not None:
                    event_callback(run_id, resubmission_result)

    def add_stage_results(stage_results: list[CheckResult]) -> None:
        for result in stage_results:
            record_event(run_id, result, db_path)
            results.append(result)
            if event_callback is not None:
                event_callback(run_id, result)

    add_stage_results(stage_3_completeness(submission, rules))
    add_stage_results(stage_4_format_validation(submission, rules))
    add_stage_results(stage_5_cross_referencing(submission, rules))
    add_stage_results(
        stage_6_risk_checks(
            submission, rules, existing_vendors, historical_vendors=historical_vendors
        )
    )
    policy_results = earlier_results + results
    non_pass_reasons = [result.reason for result in policy_results if result.status != "pass"]
    decision_result = stage_7_decision(policy_results, rules)[0]
    decision_status = decision_result.reason.split(":", 1)[0]
    if decision_status == "APPROVED":
        reasons = ["All configured checks passed."]
    else:
        reasons = non_pass_reasons
        if decision_status == "PENDING":
            reasons.append("Review or missing information is required before approval.")
    record_event(run_id, decision_result, db_path)
    results.append(decision_result)
    complete_run(run_id, decision_status, reasons, db_path)
    if event_callback is not None:
        event_callback(run_id, decision_result)
    return (
        PipelineDecision(status=decision_status, reasons=reasons, run_id=run_id),
        policy_results + [decision_result],
    )  # type: ignore[arg-type]
