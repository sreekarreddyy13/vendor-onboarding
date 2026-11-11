"""Parse and validate the shape of a JSON vendor submission."""

from __future__ import annotations

import json
from dataclasses import fields

from engine.models import VendorSubmission


def parse_submission_json(raw_json: str) -> VendorSubmission:
    """Create a submission model or raise a short, user-readable input error."""
    if not raw_json.strip():
        raise ValueError("Submission JSON is empty.")
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON: {error.msg} at line {error.lineno}, column {error.colno}.") from error

    if not isinstance(payload, dict):
        raise ValueError("The JSON root must be an object containing vendor fields.")

    allowed_fields = {item.name for item in fields(VendorSubmission)}
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise ValueError(f"Unknown submission field(s): {', '.join(unknown_fields)}.")

    required_text = ("company_name", "country")
    missing_required = [
        name for name in required_text
        if not isinstance(payload.get(name), str) or not payload[name].strip()
    ]
    if missing_required:
        raise ValueError(f"Provide a non-empty string for: {', '.join(missing_required)}.")

    text_fields = (
        "tax_id", "bank_account_number", "bank_code", "bank_account_holder_name",
        "tax_certificate_name", "bank_country", "scanned_document_file", "contact_name",
        "contact_email",
    )
    invalid_text = [name for name in text_fields if name in payload and not isinstance(payload[name], str)]
    if invalid_text:
        raise ValueError(f"These fields must be strings: {', '.join(invalid_text)}.")

    if "documents" in payload and (
        not isinstance(payload["documents"], list)
        or any(not isinstance(item, str) for item in payload["documents"])
    ):
        raise ValueError("documents must be a list of document names.")
    if "document_files" in payload and (
        not isinstance(payload["document_files"], dict)
        or any(not isinstance(key, str) or not isinstance(value, str)
               for key, value in payload["document_files"].items())
    ):
        raise ValueError("document_files must map document names to file paths.")
    if "additional_data" in payload and not isinstance(payload["additional_data"], dict):
        raise ValueError("additional_data must be a JSON object.")

    return VendorSubmission(**payload)
