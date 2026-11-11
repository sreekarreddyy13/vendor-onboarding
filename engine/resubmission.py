"""Describe which previously missing requirements were added on resubmission."""

from typing import Any

from engine.config import load_config
from engine.document_utils import provided_document_names
from engine.models import CheckResult, VendorSubmission


def describe_resubmission_changes(
    previous_run: dict[str, Any],
    submission: VendorSubmission,
    config: dict[str, Any] | None = None,
) -> CheckResult | None:
    """Compare a previous pending submission with the current one.

    Only same-tax-ID pending runs qualify. The returned result is an audit event
    for Intake; completeness and all other checks still run independently.
    """
    previous_submission = previous_run.get("submission_json", {})
    previous_tax_id = str(previous_submission.get("tax_id", "")).strip().casefold()
    current_tax_id = submission.tax_id.strip().casefold()
    if previous_run.get("status") != "PENDING" or not previous_tax_id or previous_tax_id != current_tax_id:
        return None

    rules = config or load_config()
    country = str(previous_submission.get("country", submission.country))
    required_fields = rules.get("required_fields", {}).get(country, [])
    required_documents = rules.get("required_documents", {}).get(country, [])

    prior_missing_fields = [
        field for field in required_fields if not previous_submission.get(field)
    ]
    current_values = submission.__dict__
    provided_fields = [field for field in prior_missing_fields if current_values.get(field)]
    still_missing_fields = [field for field in prior_missing_fields if not current_values.get(field)]

    def document_set(payload: dict[str, Any]) -> set[str]:
        return provided_document_names(payload.get("document_files", {}))

    previous_documents = document_set(previous_submission)
    current_documents = provided_document_names(submission.document_files)
    prior_missing_documents = [
        document for document in required_documents
        if document.casefold() not in previous_documents
    ]
    provided_documents = [
        document for document in prior_missing_documents
        if document.casefold() in current_documents
    ]
    still_missing_documents = [
        document for document in prior_missing_documents
        if document.casefold() not in current_documents
    ]

    newly_provided = [*(f"field: {field}" for field in provided_fields),
                      *(f"document: {document}" for document in provided_documents)]
    still_missing = [*(f"field: {field}" for field in still_missing_fields),
                     *(f"document: {document}" for document in still_missing_documents)]
    previous_id = previous_run.get("id", "unknown run")
    status = "warn" if still_missing else "pass"
    reason = (
        f"Resubmission detected for the same tax ID; linked to previous PENDING run {previous_id}. "
        f"Previously missing items now provided: {', '.join(newly_provided) if newly_provided else 'none'}. "
        f"Still missing from that attempt: {', '.join(still_missing) if still_missing else 'none'}."
    )
    return CheckResult(
        stage="Intake",
        check_name="resubmission_changes",
        status=status,
        reason=reason,
    )
