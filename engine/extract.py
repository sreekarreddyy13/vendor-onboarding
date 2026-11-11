"""Stage 2: read uploaded documents and compare extracted fields with the form."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from engine.config import PROJECT_ROOT
from engine.llm import LLMError, LLMResult, extract_json_from_document
from engine.models import CheckResult, VendorSubmission


DOCUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string"},
        "tax_id": {"type": "string"},
        "account_holder_name": {"type": "string"},
        "account_number": {"type": "string"},
        "bank_code": {"type": "string"},
        "document_type": {"type": "string"},
    },
    "required": ["company_name", "tax_id", "account_holder_name", "account_number", "bank_code", "document_type"],
}
FIELDS = tuple(DOCUMENT_SCHEMA["properties"])


@dataclass
class DocumentReading:
    document_key: str
    file_path: str
    extracted: dict[str, str] = field(default_factory=dict)
    from_cache: bool = False


@dataclass
class DocumentReadingResult:
    checks: list[CheckResult] = field(default_factory=list)
    documents: list[DocumentReading] = field(default_factory=list)
    # This shows the form fallback used when extraction is unavailable.
    form_values: dict[str, str] = field(default_factory=dict)


def _parse_json(text: str) -> dict[str, str]:
    parsed = json.loads(text)
    if not isinstance(parsed, dict) or set(parsed) != set(FIELDS):
        raise ValueError("Response must contain exactly the six required fields.")
    if any(not isinstance(parsed[field_name], str) for field_name in FIELDS):
        raise ValueError("Every extracted field must be a string.")
    if not parsed["document_type"].strip():
        raise ValueError("document_type cannot be empty.")
    return {field_name: parsed[field_name].strip() for field_name in FIELDS}


def _form_values(submission: VendorSubmission) -> dict[str, str]:
    return {
        "company_name": submission.company_name,
        "tax_id": submission.tax_id,
        "account_holder_name": submission.bank_account_holder_name or submission.company_name,
        "account_number": submission.bank_account_number,
        "bank_code": submission.bank_code,
    }


def read_documents(
    submission: VendorSubmission,
    llm_function: Callable[[str | Path, str, dict[str, Any]], LLMResult] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> DocumentReadingResult:
    """Extract each referenced file; extraction failures stay warnings, never crashes."""
    llm_call = llm_function or extract_json_from_document
    result = DocumentReadingResult(form_values=_form_values(submission))
    document_entries = list(submission.document_files.items())
    if submission.scanned_document_file and all(
        submission.scanned_document_file != path for _, path in document_entries
    ):
        document_entries.append(("scanned_document", submission.scanned_document_file))

    if not document_entries:
        result.checks.append(CheckResult(
            "Document reading", "document_inputs", "pass",
            "No separate document files were supplied; the form values remain available for validation.",
        ))
        return result

    for document_key, raw_path in document_entries:
        file_path = Path(raw_path)
        if not file_path.is_absolute():
            file_path = project_root / file_path
        document = DocumentReading(document_key=document_key, file_path=str(file_path))
        result.documents.append(document)
        if not file_path.is_file():
            result.checks.append(CheckResult(
                "Document reading", f"document_{document_key}", "warn",
                f"Extraction skipped because the referenced file '{raw_path}' was not found; submitted form values were retained.",
            ))
            continue

        prompt = (
            "Read this vendor onboarding document. Return exactly the requested JSON fields. "
            "Use an empty string for fields that are not present. Do not infer missing identifiers. "
            f"The only allowed keys are: {', '.join(FIELDS)}. The expected document category is '{document_key}'."
        )
        try:
            first_response = llm_call(file_path, prompt, DOCUMENT_SCHEMA)
            try:
                extracted = _parse_json(first_response.text)
                response = first_response
            except (json.JSONDecodeError, ValueError):
                retry_prompt = (
                    prompt + " Your previous response was invalid. Return valid JSON only, with exactly these keys: "
                    + ", ".join(FIELDS) + "."
                )
                response = llm_call(file_path, retry_prompt, DOCUMENT_SCHEMA)
                extracted = _parse_json(response.text)
            document.extracted = extracted
            document.from_cache = response.from_cache
            cache_note = "Result came from cache." if response.from_cache else "Result came from a fresh model response."
            result.checks.append(CheckResult(
                "Document reading", f"document_{document_key}", "pass",
                f"Extracted structured fields from '{raw_path}'. {cache_note}",
            ))
            for field_name, submitted_value in result.form_values.items():
                extracted_value = extracted.get(field_name, "")
                if submitted_value and extracted_value and extracted_value.strip().casefold() != submitted_value.strip().casefold():
                    result.checks.append(CheckResult(
                        "Document reading", f"{document_key}_{field_name}_comparison", "warn",
                        f"The document's {field_name.replace('_', ' ')} ('{extracted_value}') differs from the submitted form value ('{submitted_value}').",
                    ))
        except Exception as error:
            if isinstance(error, LLMError):
                detail = str(error)
            elif isinstance(error, (json.JSONDecodeError, ValueError)):
                detail = "the structured response could not be validated after one retry"
            else:
                detail = f"the extraction service returned {type(error).__name__}"
            reason = (
                f"Document extraction was skipped for '{raw_path}': {detail}. "
                "Submitted form values were retained."
            )
            result.checks.append(CheckResult(
                "Document reading", f"document_{document_key}", "warn", reason,
            ))
    return result
