"""Stage 8: draft vendor-facing email from deterministic check results."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from engine.config import load_config
from engine.document_utils import provided_document_names
from engine.llm import LLMResult, generate_text
from engine.models import CheckResult, VendorSubmission


@dataclass(frozen=True)
class EmailDraft:
    body: str
    source: str
    from_cache: bool = False


def _vendor_safe_issues(
    submission: VendorSubmission,
    status: str,
    checks: Iterable[CheckResult],
) -> list[dict[str, str]]:
    """Translate failed checks into issues safe and clear to show a vendor."""
    config = load_config()
    issue_checks = [
        check for check in checks
        if check.status != "pass"
        and check.stage != "Decision"
        and check.check_name != "resubmission_changes"
    ]
    tax_invalid = any(check.check_name == "tax_id_format" and check.status == "fail" for check in issue_checks)
    name_mismatch = any(
        check.check_name.startswith("name_match_") and check.status == "fail"
        for check in issue_checks
    )
    combined_identity_rejection = status == "REJECTED" and tax_invalid and name_mismatch
    issues: list[dict[str, str]] = []
    identity_issue_added = False

    for check in issue_checks:
        name = check.check_name
        issue = check.reason
        if combined_identity_rejection and (name == "tax_id_format" or name.startswith("name_match_")):
            if not identity_issue_added:
                issue = (
                    "The company name and tax registration details could not be verified together. "
                    "The application cannot proceed."
                )
                issues.append({"stage": check.stage, "check": "registration_details", "issue": issue})
                identity_issue_added = True
            continue
        if name == "bank_account_reuse":
            # Never reveal another organization's identity or internal matching evidence.
            issue = (
                "The bank account details could not be accepted for this application. "
                "The application cannot proceed."
            )
        elif name == "blocked_country":
            issue = "We are unable to proceed with this application based on the information submitted."
        elif name == "duplicate_tax_id":
            issue = (
                "This tax ID is already registered in our system. Please confirm it is correct "
                "or contact the onboarding team for help."
            )
        elif name == "required_fields":
            missing = [
                field for field in config.get("required_fields", {}).get(submission.country, [])
                if not getattr(submission, field, "")
            ]
            issue = f"Please provide the missing information: {', '.join(missing)}." if missing else check.reason
        elif name == "required_documents":
            required = config.get("required_documents", {}).get(submission.country, [])
            provided = provided_document_names(submission.document_files)
            missing = [item for item in required if item.casefold() not in provided]
            issue = f"Please provide the missing document(s): {', '.join(missing)}." if missing else check.reason
        elif name == "tax_id_format":
            tax_name = config.get("tax_id_formats", {}).get(submission.country, {}).get("name", "tax ID")
            issue = (
                f"The submitted tax ID {submission.tax_id!r} does not match the expected "
                f"{tax_name} format for {submission.country}."
            )
        elif name == "name_match_bank_account_holder":
            issue = (
                f"The bank account holder name {submission.bank_account_holder_name!r} does not match "
                f"the submitted company name {submission.company_name!r} closely enough. "
                "Please correct the information or provide supporting documentation."
            )
        elif name == "name_match_tax_certificate":
            issue = (
                f"The tax certificate name {submission.tax_certificate_name!r} does not match "
                f"the submitted company name {submission.company_name!r} closely enough. "
                "Please correct the information or provide supporting documentation."
            )
        elif name == "bank_country_match":
            issue = (
                f"The bank country {submission.bank_country!r} conflicts with the claimed country "
                f"{submission.country!r}. Please correct the country information."
            )
        elif name.startswith("document_") and not name.endswith("_comparison"):
            document_type = name.removeprefix("document_").replace("_", " ")
            issue = (
                f"We could not read the {document_type} document. Please send a clear PDF or image copy."
            )
        issues.append({"stage": check.stage, "check": name, "issue": issue})

    return issues


def _fallback_email(
    submission: VendorSubmission,
    status: str,
    issues: list[dict[str, str]],
) -> str:
    greeting = f"Hello {submission.contact_name.strip()}," if submission.contact_name.strip() else "Hello,"
    lines = [greeting, "", f"Thank you for submitting your vendor application for {submission.company_name}.", ""]
    if status == "PENDING":
        lines.extend(["We need the following information before we can complete our review:", ""])
        lines.extend(f"- {item['issue']}" for item in issues)
        lines.extend([
            "",
            "Next step: Please reply to this email with the requested documents or corrected information attached.",
        ])
    else:
        lines.extend(["After review, we are unable to proceed with this vendor application.", ""])
        lines.extend(f"- {item['issue']}" for item in issues)
        lines.extend([
            "",
            "If you have questions, please contact the vendor onboarding team. The application cannot proceed.",
        ])
    lines.extend(["", "Kind regards,", "Vendor Onboarding Team"])
    return "\n".join(lines)


def _email_is_usable(body: str, status: str, issues: list[dict[str, str]]) -> bool:
    """Reject drafts that omit issues or expose internal rejection details."""
    normalized_body = body.casefold()
    if not all(issue["issue"].casefold() in normalized_body for issue in issues):
        return False
    if status == "PENDING":
        return "reply to this email" in normalized_body and "attached" in normalized_body
    if "cannot proceed" not in normalized_body and "unable to proceed" not in normalized_body:
        return False
    forbidden_phrases = ("fraud", "internal risk", "another vendor", "different vendor", "shared account")
    return not any(phrase in normalized_body for phrase in forbidden_phrases)


def draft_vendor_email(
    submission: VendorSubmission,
    status: str,
    checks: Iterable[CheckResult],
    llm_function: Callable[[str], LLMResult] | None = None,
) -> EmailDraft | None:
    """Draft an email for pending or rejected runs; fall back without interrupting."""
    if status == "APPROVED":
        return None

    issues = _vendor_safe_issues(submission, status, checks)
    if not issues:
        issues = [{
            "stage": "Decision",
            "check": "review_required",
            "issue": "The application needs additional review before it can proceed.",
        }]
    prompt = (
        "Write a professional, polite vendor onboarding email in plain text. Use only the supplied "
        "vendor details and issue list; do not invent facts, documents, URLs, or deadlines. Keep every "
        "issue specific and faithful to the list. Include every issue statement verbatim as a bullet. "
        "For PENDING, tell the vendor to reply to this email "
        "with the requested documents or corrected information attached. For REJECTED, clearly state "
        "that the application cannot proceed and do not suggest resubmitting. Never mention fraud, "
        "internal risk checks, another vendor, or private details about another organization. "
        f"Status: {status}\nVendor: {submission.company_name}\n"
        f"Contact name: {submission.contact_name or 'Vendor'}\n"
        f"Structured issues: {json.dumps(issues, ensure_ascii=False)}"
    )
    try:
        response = (llm_function or generate_text)(prompt)
        body = response.text.strip()
        if body and _email_is_usable(body, status, issues):
            return EmailDraft(body=body, source="Gemini", from_cache=response.from_cache)
    except Exception:
        # The template is the safe fallback for missing keys, API errors, and bad responses.
        pass
    return EmailDraft(
        body=_fallback_email(submission, status, issues),
        source="fallback template",
        from_cache=False,
    )
