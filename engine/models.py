"""Small data models shared by the onboarding pipeline."""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class VendorSubmission:
    """Vendor data received at intake; optional fields vary by country."""

    company_name: str
    country: str
    tax_id: str = ""
    bank_account_number: str = ""
    bank_code: str = ""
    bank_account_holder_name: str = ""
    tax_certificate_name: str = ""
    bank_country: str = ""
    documents: list[str] = field(default_factory=list)
    document_files: dict[str, str] = field(default_factory=dict)
    scanned_document_file: str = ""
    contact_name: str = ""
    contact_email: str = ""
    additional_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    """Explainable result from one pipeline check."""

    stage: str
    check_name: str
    status: Literal["pass", "fail", "warn"]
    reason: str


@dataclass
class PipelineDecision:
    """Final status and the human-readable reasons behind it."""

    status: Literal["APPROVED", "PENDING", "REJECTED"]
    reasons: list[str] = field(default_factory=list)
    run_id: str = ""
