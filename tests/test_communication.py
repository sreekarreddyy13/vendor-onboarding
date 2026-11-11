from engine.communication import draft_vendor_email
from engine.llm import LLMResult
from engine.models import CheckResult, VendorSubmission


def test_pending_email_lists_issues_and_next_step() -> None:
    submission = VendorSubmission(
        company_name="Example Components Pvt Ltd",
        country="India",
        documents=["gst_registration"],
    )
    issues = [CheckResult(
        "Completeness", "required_documents", "fail",
        "Missing required documents: certificate_of_incorporation, bank_letter.",
    )]
    draft = draft_vendor_email(submission, "PENDING", issues)

    assert draft is not None
    assert "bank_letter" in draft.body
    assert "certificate_of_incorporation" in draft.body
    assert "reply to this email" in draft.body.casefold()
    assert "attached" in draft.body.casefold()


def test_rejection_email_does_not_disclose_account_collision_owner() -> None:
    submission = VendorSubmission(company_name="Example Components", country="India")
    checks = [CheckResult(
        "Risk checks", "bank_account_reuse", "fail",
        "This account is used by Aarav Industrial Supplies Pvt Ltd (SAMPLE-001).",
    )]
    draft = draft_vendor_email(submission, "REJECTED", checks)

    assert draft is not None
    assert "cannot proceed" in draft.body.casefold()
    assert "Aarav Industrial Supplies" not in draft.body
    assert "SAMPLE-001" not in draft.body
    assert "fraud" not in draft.body.casefold()


def test_unusable_llm_email_uses_safe_template() -> None:
    submission = VendorSubmission(company_name="Example Components", country="India")
    checks = [CheckResult("Format validation", "tax_id_format", "fail", "Tax ID is invalid.")]

    def bad_draft(_prompt: str) -> LLMResult:
        return LLMResult("Please contact us for more information.")

    draft = draft_vendor_email(submission, "PENDING", checks, llm_function=bad_draft)

    assert draft is not None
    assert draft.source == "fallback template"
    assert "tax ID" in draft.body
    assert "reply to this email" in draft.body.casefold()
