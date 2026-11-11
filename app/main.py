"""Streamlit interface for running deterministic vendor validation."""

import html
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db import (
    DEFAULT_DB_PATH,
    create_run,
    get_run,
    get_run_events,
    get_linked_runs,
    initialize_database,
    list_runs,
    record_event,
    reset_demo_data,
    save_drafted_email,
)
from engine.models import CheckResult, PipelineDecision, VendorSubmission
from engine.intake import parse_submission_json
from engine.config import load_config
from engine.extract import read_documents
from engine.resubmission import describe_resubmission_changes
from engine.communication import draft_vendor_email
from engine.validation import run_pipeline


STAGE_NAMES = [
    "Intake",
    "Document reading",
    "Completeness check",
    "Format validation",
    "Cross-referencing",
    "Risk checks",
    "Decision",
    "Communication",
    "Audit log",
]
STAGE_INDEX = {
    "Intake": 0,
    "Completeness": 2,
    "Format validation": 3,
    "Cross-referencing": 4,
    "Risk checks": 5,
    "Decision": 6,
}
STAGE_DELAY_SECONDS = 0.5
LOGGER = logging.getLogger(__name__)
DEMO_SCENARIOS = [
    ("Happy path", "happy.json"),
    ("Name normalisation", "name_suffix_match.json"),
    ("Shared bank account", "bank_account_collision.json"),
    ("Resubmission · initial pending attempt", "resubmission_missing_bank_letter.json"),
    ("Resubmission · document supplied", "resubmission_bank_letter_provided.json"),
    ("Country conflict", "indian_vendor_us_ein.json"),
    ("Missing documents", "missing_bank_letter.json"),
]

STATUS_VIEW = {
    "waiting": ("○", "Waiting"),
    "running": ("◌", "Running"),
    "passed": ("✓", "Passed"),
    "warning": ("!", "Warning"),
    "failed": ("×", "Failed"),
}


def _inject_styles() -> None:
    """Apply a compact visual system shared by the app pages."""
    st.markdown(
        """
        <style>
        .stApp { background: #f5f7fb; }
        [data-testid="stSidebar"] { background: #101a2b; }
        [data-testid="stSidebar"] * { color: #e8eef8; }
        .brand-kicker { color: #7283a0; font-size: .76rem; font-weight: 700;
            letter-spacing: .13em; text-transform: uppercase; margin-bottom: .4rem; }
        .hero-title { color: #142238; font-size: 2.05rem; font-weight: 750;
            letter-spacing: -.04em; margin: 0; }
        .hero-copy { color: #64748b; margin: .35rem 0 1.3rem; }
        .status-chip { display: inline-flex; align-items: center; gap: .5rem;
            border-radius: 999px; padding: .7rem 1.15rem; font-weight: 750;
            font-size: 1.15rem; }
        .status-approved { color: #087443; background: #e1f5eb; }
        .status-pending { color: #9a5b00; background: #fff1d1; }
        .status-rejected { color: #a32b36; background: #fde7e9; }
        .decision-card { background: white; border: 1px solid #e4eaf2;
            border-radius: 18px; padding: 1.25rem 1.4rem; margin-top: 1rem;
            box-shadow: 0 8px 28px rgba(22, 39, 66, .05); }
        .stage-title { color: #17263d; font-weight: 700; font-size: 1rem; }
        .stage-meta { color: #718096; font-size: .78rem; }
        .stage-icon { display: inline-flex; width: 2rem; height: 2rem;
            align-items: center; justify-content: center; border-radius: 50%;
            font-weight: 800; }
        .icon-waiting { background:#eef2f7; color:#8996a9; }
        .icon-running { background:#e5efff; color:#2463c7; }
        .icon-passed { background:#ddf4e8; color:#11804d; }
        .icon-warning { background:#fff0cf; color:#a96700; }
        .icon-failed { background:#fde5e7; color:#b42d39; }
        .check-line { border-top: 1px solid #edf1f6; padding: .55rem 0 .1rem;
            margin-top: .5rem; color:#40516a; font-size:.88rem; }
        .check-label { color:#1e2e45; font-weight:650; }
        .check-reason { color:#66758a; margin:.18rem 0 0 0; }
        .decision-reason { color:#4b5d73; padding:.45rem 0; border-top:1px solid #edf1f6; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _load_gemini_key() -> str | None:
    """Load the optional key from dotenv, environment, then Streamlit secrets."""
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("GEMINI_API_KEY"):
        # The original development .env may sit beside the project directory.
        load_dotenv(PROJECT_ROOT.parent / ".env")
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        return api_key
    try:
        return st.secrets.get("GEMINI_API_KEY")
    except Exception:
        # Streamlit raises when no secrets file or deployed secret is configured.
        return None


def _initial_stage_state() -> list[dict[str, object]]:
    return [{"status": "waiting", "checks": []} for _ in STAGE_NAMES]


def _stage_icon(status: str) -> tuple[str, str]:
    icon, label = STATUS_VIEW.get(status, STATUS_VIEW["waiting"])
    return icon, label


def _render_stage(slot, index: int, state: dict[str, object]) -> None:
    status = str(state["status"])
    icon, label = _stage_icon(status)
    icon = html.escape(icon)
    name = html.escape(STAGE_NAMES[index])
    checks = state.get("checks", [])
    with slot.container(border=True):
        left, right = st.columns([0.08, 0.92], vertical_alignment="center")
        with left:
            st.markdown(
                f'<div class="stage-icon icon-{status}">{icon}</div>',
                unsafe_allow_html=True,
            )
        with right:
            st.markdown(
                f'<div class="stage-title">{index + 1:02d} &nbsp; {name}</div>'
                f'<div class="stage-meta">{label}</div>',
                unsafe_allow_html=True,
            )
        for check in checks:
            check_status = str(check["status"])
            check_label = {"pass": "Passed", "warn": "Warning", "fail": "Failed"}.get(
                check_status, check_status.title()
            )
            check_name = html.escape(str(check["name"]))
            reason = html.escape(str(check["reason"]))
            st.markdown(
                '<div class="check-line">'
                f'<div><span class="check-label">{check_name}</span>'
                f' &nbsp;·&nbsp; {html.escape(check_label)}</div>'
                f'<div class="check-reason">{reason}</div></div>',
                unsafe_allow_html=True,
            )


def _add_check(index: int, result: CheckResult) -> None:
    state = st.session_state.stage_state[index]
    checks = list(state["checks"])
    checks.append({"name": result.check_name, "status": result.status, "reason": result.reason})
    state["checks"] = checks
    st.session_state.stage_state[index] = state


def _status_from_checks(checks: list[dict[str, object]]) -> str:
    statuses = {str(check["status"]) for check in checks}
    if "fail" in statuses:
        return "failed"
    if "warn" in statuses:
        return "warning"
    return "passed"


def _set_stage(slots, index: int, status: str) -> None:
    state = st.session_state.stage_state[index]
    state["status"] = status
    st.session_state.stage_state[index] = state
    _render_stage(slots[index], index, state)


def _record_simple_stage(
    run_id: str,
    slots,
    index: int,
    result: CheckResult,
    db_path: Path,
) -> None:
    _set_stage(slots, index, "running")
    time.sleep(0.12)
    record_event(run_id, result, db_path)
    _add_check(index, result)
    _set_stage(slots, index, _status_from_checks(st.session_state.stage_state[index]["checks"]))
    time.sleep(STAGE_DELAY_SECONDS)


def _document_keys(country: str, config: dict) -> dict[str, str]:
    """Map the three upload controls to this country's configured document names."""
    required = config.get("required_documents", {}).get(country, [])
    bank_key = next((name for name in required if "bank" in name.casefold()), "bank_letter")
    incorporation_key = next(
        (name for name in required if "incorpor" in name.casefold()),
        "certificate_of_incorporation",
    )
    tax_key = next(
        (name for name in required if name not in {bank_key, incorporation_key}),
        "tax_certificate",
    )
    return {
        "tax_certificate": tax_key,
        "bank_letter": bank_key,
        "incorporation_certificate": incorporation_key,
    }


def _document_category(document_name: str) -> str:
    normalized = document_name.casefold()
    if "bank" in normalized:
        return "bank_letter"
    if "incorpor" in normalized:
        return "incorporation_certificate"
    return "tax_certificate"


def _load_demo_submission(filename: str) -> None:
    """Prefill form fields and attach the sample's existing document paths."""
    sample_path = PROJECT_ROOT / "data" / "samples" / filename
    submission = parse_submission_json(sample_path.read_text(encoding="utf-8"))
    extra = submission.additional_data
    form_values = {
        "form_company_name": submission.company_name,
        "form_country": submission.country,
        "form_address": str(extra.get("address", "")),
        "form_contact_name": submission.contact_name,
        "form_contact_email": submission.contact_email,
        "form_phone": str(extra.get("phone", "")),
        "form_tax_id": submission.tax_id,
        "form_tax_certificate_name": submission.tax_certificate_name,
        "form_bank_account_holder_name": submission.bank_account_holder_name,
        "form_bank_name": str(extra.get("bank_name", "")),
        "form_bank_account_number": submission.bank_account_number,
        "form_bank_code": submission.bank_code,
        "form_bank_country": submission.bank_country,
    }
    for key, value in form_values.items():
        st.session_state[key] = value
    st.session_state["demo_document_files"] = dict(submission.document_files)
    st.session_state["loaded_demo_filename"] = filename
    st.session_state["last_decision"] = None
    st.session_state["last_email_draft"] = None
    st.session_state["stage_state"] = _initial_stage_state()
    # Changing uploader keys clears any manually selected files from an earlier form.
    st.session_state["upload_generation"] = st.session_state.get("upload_generation", 0) + 1


def _save_uploaded_file(uploaded_file, document_key: str) -> str:
    """Persist one user upload with a collision-resistant name under data/uploads."""
    upload_dir = PROJECT_ROOT / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(uploaded_file.name).suffix.lower()
    stored_name = f"{document_key}_{uuid4().hex}{suffix}"
    destination = upload_dir / stored_name
    destination.write_bytes(uploaded_file.getvalue())
    return destination.relative_to(PROJECT_ROOT).as_posix()


def _build_form_submission(
    country: str,
    values: dict[str, str],
    uploads: dict[str, object],
    keep_demo_files: dict[str, bool],
    demo_files: dict[str, str],
    config: dict,
) -> VendorSubmission:
    """Build the existing pipeline model from optional form values and files."""
    document_files = dict(demo_files)

    # A sample attachment stays in the submission unless the user unchecks it
    # or replaces it with an uploaded file.
    demo_categories = {
        _document_category(name): name
        for name in demo_files
    }
    for category, document_name in demo_categories.items():
        if not keep_demo_files.get(category, True):
            document_files.pop(document_name, None)

    configured_keys = _document_keys(country, config)
    for category, uploaded_file in uploads.items():
        if uploaded_file is None:
            continue
        category_names = {
            name for name in document_files
            if _document_category(name) == category
        }
        for name in category_names:
            document_files.pop(name, None)
        target_key = configured_keys[category]
        document_files[target_key] = _save_uploaded_file(uploaded_file, target_key)

    return VendorSubmission(
        company_name=values.get("form_company_name", "").strip(),
        country=country,
        tax_id=values.get("form_tax_id", "").strip(),
        bank_account_number=values.get("form_bank_account_number", "").strip(),
        bank_code=values.get("form_bank_code", "").strip(),
        bank_account_holder_name=values.get("form_bank_account_holder_name", "").strip(),
        tax_certificate_name=values.get("form_tax_certificate_name", "").strip(),
        bank_country=values.get("form_bank_country", "").strip(),
        documents=list(document_files),
        document_files=document_files,
        contact_name=values.get("form_contact_name", "").strip(),
        contact_email=values.get("form_contact_email", "").strip(),
        additional_data={
            "address": values.get("form_address", "").strip(),
            "phone": values.get("form_phone", "").strip(),
            "bank_name": values.get("form_bank_name", "").strip(),
        },
    )


def _render_submission_inputs() -> tuple[VendorSubmission | None, bool]:
    """Render demo loading, the vendor form, and the advanced JSON route."""
    config = load_config()
    countries = sorted(
        set(config.get("tax_id_formats", {}))
        & set(config.get("bank_code_formats", {}))
    )
    if "form_country" not in st.session_state:
        st.session_state["form_country"] = "India" if "India" in countries else ""
    st.session_state.setdefault("upload_generation", 0)
    st.session_state.setdefault("demo_document_files", {})
    previous_country = st.session_state.get("last_country_selection")
    current_country = st.session_state["form_country"]
    current_bank_country = st.session_state.get("form_bank_country", "")
    if current_country != previous_country:
        if not current_bank_country or current_bank_country == previous_country:
            st.session_state["form_bank_country"] = current_country
        st.session_state["last_country_selection"] = current_country

    with st.container(border=True):
        st.subheader("Load demo scenario")
        demo_labels = [label for label, _ in DEMO_SCENARIOS]
        demo_columns = st.columns([0.72, 0.28], vertical_alignment="bottom")
        with demo_columns[0]:
            selected_demo = st.selectbox(
                "Choose a prepared submission", demo_labels,
                key="demo_scenario_selection",
                help="Loading a scenario fills the form and attaches its sample documents. You can edit it before review.",
            )
        with demo_columns[1]:
            load_clicked = st.button("Load", use_container_width=True)
        if load_clicked:
            filename = dict(DEMO_SCENARIOS)[selected_demo]
            try:
                _load_demo_submission(filename)
                st.success("Scenario loaded. Review or edit the fields and attachments, then submit for review.")
            except (OSError, ValueError) as error:
                st.error(f"Could not load this demo scenario: {error}")

    # A regular (non-form) country selector lets the country-specific labels
    # react immediately. Streamlit form fields only update after submission.
    with st.container(border=True):
        st.subheader("Vendor submission")
        country_options = ["", *countries]
        country_col, hint_col = st.columns([0.5, 0.5], vertical_alignment="bottom")
        with country_col:
            country = st.selectbox(
                "Country",
                country_options,
                format_func=lambda value: value or "Select a country",
                key="form_country",
                help="Choose the country the vendor claims. Leave blank to demonstrate missing information.",
            )
        with hint_col:
            st.caption("Country selection updates the tax ID and bank code labels. Fields can be left blank for completeness checks.")

        tax_rule = config.get("tax_id_formats", {}).get(country, {})
        tax_name = str(tax_rule.get("name", "tax ID"))
        bank_rule = config.get("bank_code_formats", {}).get(country, {})
        bank_name = str(
            bank_rule.get("name")
            or (bank_rule.get("any_of") or [{}])[0].get("name", "bank code")
        )
        tax_label = {"GSTIN": "GSTIN", "EIN": "EIN", "VAT": "VAT number"}.get(tax_name, tax_name)
        bank_label = {
            "IFSC": "IFSC",
            "routing_number": "Routing number",
            "sort_code": "Sort code",
        }.get(bank_name, bank_name.replace("_", " ").title())
        upload_generation = st.session_state["upload_generation"]
        document_keys = _document_keys(country, config)
        demo_files = st.session_state.get("demo_document_files", {})

        uploads: dict[str, object] = {}
        keep_demo_files: dict[str, bool] = {}
        form_submitted = False
        with st.form("vendor_submission_form", clear_on_submit=False):
            st.markdown("#### Company details")
            company_col, address_col = st.columns(2)
            with company_col:
                st.text_input(
                    "Company name", key="form_company_name",
                    help="Registered or trading name supplied by the vendor.",
                )
                st.caption("Use the legal or trading name shown on supporting records.")
                st.text_input(
                    "Contact name", key="form_contact_name",
                    help="Person procurement can contact about this application.",
                )
                st.caption("Person responsible for onboarding questions.")
            with address_col:
                st.text_input(
                    "Address", key="form_address",
                    help="Business or registered address, if available.",
                )
                st.caption("Business or registered address, if available.")
                st.text_input(
                    "Contact email", key="form_contact_email",
                    placeholder="name@company.com",
                    help="The email address for the follow-up draft.",
                )
                st.caption("Used for the follow-up email draft.")
            st.text_input(
                "Phone", key="form_phone", placeholder="Include country code when known.",
                help="Optional contact phone number.",
            )
            st.caption("Include the international dialing code when known.")

            st.markdown("#### Tax information")
            tax_col, certificate_name_col = st.columns(2)
            with tax_col:
                placeholders = {
                    "India": "e.g. 27AABCU9603R1Z1",
                    "US": "e.g. 12-3456789",
                    "UK": "e.g. GB123456789",
                }
                st.text_input(
                    tax_label, key="form_tax_id", placeholder=placeholders.get(country, "Enter tax ID"),
                    help=f"Tax identifier format is checked against the configured rules for {country or 'the selected country'}.",
                )
                st.caption(f"Enter the {tax_label} shown on the registration record.")
            with certificate_name_col:
                st.text_input(
                    "Name on tax certificate", key="form_tax_certificate_name",
                    placeholder="As printed on the certificate",
                    help="Used to compare the registered name with the form name.",
                )
                st.caption("Copy the company name exactly as printed on the certificate.")

            st.markdown("#### Bank details")
            bank_col, account_col = st.columns(2)
            with bank_col:
                st.text_input(
                    "Account holder name", key="form_bank_account_holder_name",
                    help="Name registered on the bank account.",
                )
                st.caption("Enter the account holder exactly as shown by the bank.")
                st.text_input(
                    "Bank name", key="form_bank_name", placeholder="e.g. Example National Bank",
                    help="Bank or financial institution name.",
                )
                st.caption("Name of the bank or financial institution.")
            with account_col:
                st.text_input(
                    "Account number", key="form_bank_account_number",
                    help="Vendor's account number. This demo stores sample values locally.",
                )
                st.caption("Enter the account number from the bank confirmation.")
                bank_placeholders = {
                    "India": "e.g. HDFC0001234",
                    "US": "e.g. 021000021",
                    "UK": "e.g. 12-34-56",
                }
                st.text_input(
                    bank_label, key="form_bank_code",
                    placeholder=bank_placeholders.get(country, "Enter bank code"),
                    help=f"Enter the configured {bank_label} format for {country or 'the selected country'}.",
                )
                st.caption(f"Enter the bank's {bank_label} for {country or 'the selected country'}.")
            bank_country_options = ["", *countries]
            st.selectbox(
                "Bank country", bank_country_options,
                format_func=lambda value: value or "Select bank country",
                key="form_bank_country",
                help="Compared with the claimed country. Leave blank to demonstrate missing information.",
            )
            st.caption("Compared with the vendor's claimed country.")

            st.markdown("#### Documents")
            upload_specs = [
                ("tax_certificate", "Tax certificate", "A tax registration or tax ID document."),
                ("bank_letter", "Bank confirmation letter", "A bank letter showing the account details."),
                ("incorporation_certificate", "Incorporation certificate", "Proof of company registration."),
            ]
            for category, label, help_text in upload_specs:
                matching_demo = next(
                    (
                        (name, path) for name, path in demo_files.items()
                        if _document_category(name) == category
                    ),
                    None,
                )
                if matching_demo:
                    demo_name, demo_path = matching_demo
                    st.caption(f"Attached demo file: {Path(demo_path).name}")
                    keep_demo_files[category] = st.checkbox(
                        "Keep attached demo file",
                        value=True,
                        key=f"keep_demo_{category}_{upload_generation}",
                        help="Uncheck to submit without this sample document.",
                    )
                else:
                    keep_demo_files[category] = False
                uploads[category] = st.file_uploader(
                    f"{label} · {document_keys[category]}",
                    type=["pdf", "png", "jpg", "jpeg"],
                    key=f"upload_{category}_{upload_generation}",
                    help=help_text,
                )
                st.caption("Accepted formats: PDF, PNG, JPG.")

            form_submitted = st.form_submit_button(
                "Submit for review", type="primary", use_container_width=True
            )

        if form_submitted:
            try:
                value_keys = (
                    "form_company_name", "form_address", "form_contact_name",
                    "form_contact_email", "form_phone", "form_tax_id",
                    "form_tax_certificate_name", "form_bank_account_holder_name",
                    "form_bank_name", "form_bank_account_number", "form_bank_code",
                    "form_bank_country",
                )
                values = {key: str(st.session_state.get(key, "")) for key in value_keys}
                return _build_form_submission(
                    country,
                    values,
                    uploads,
                    keep_demo_files,
                    dict(st.session_state.get("demo_document_files", {})),
                    config,
                ), True
            except OSError as error:
                st.error(f"Could not save an uploaded document: {error}")
                return None, False

    with st.expander("Advanced: paste or upload JSON", expanded=False):
        with st.form("advanced_json_form", clear_on_submit=False):
            pasted_json = st.text_area(
                "Paste a vendor submission JSON", height=180, placeholder="{\"company_name\": \"Example Co\", ...}",
                help="Use document_files to reference supporting PDF or image paths.",
            )
            uploaded_json = st.file_uploader(
                "Or upload a JSON submission", type=["json"], key="advanced_json_upload",
                help="If both are supplied, the uploaded JSON file is used.",
            )
            json_submitted = st.form_submit_button("Run JSON submission", use_container_width=True)
        if json_submitted:
            try:
                raw_json = uploaded_json.getvalue().decode("utf-8") if uploaded_json else pasted_json
                return parse_submission_json(raw_json), True
            except (TypeError, ValueError, UnicodeDecodeError) as error:
                st.error(f"Could not read this submission: {error}")
                return None, False

    return None, False


def _render_decision(decision: PipelineDecision) -> None:
    status_class = {
        "APPROVED": "status-approved",
        "PENDING": "status-pending",
        "REJECTED": "status-rejected",
    }[decision.status]
    icon = {"APPROVED": "✓", "PENDING": "!", "REJECTED": "×"}[decision.status]
    reasons = "".join(
        f'<div class="decision-reason">{html.escape(reason)}</div>'
        for reason in decision.reasons
    )
    st.markdown(
        f'<div class="decision-card"><div class="brand-kicker">Final decision</div>'
        f'<span class="status-chip {status_class}">{icon} {decision.status}</span>'
        f'<div style="margin-top:1rem">{reasons}</div>'
        f'<div class="stage-meta" style="margin-top:.8rem">Run ID: '
        f'{html.escape(decision.run_id)}</div></div>',
        unsafe_allow_html=True,
    )
    collision_reason = next(
        (reason for reason in decision.reasons if "used by a different vendor:" in reason),
        None,
    )
    if collision_reason:
        st.error(f"Bank account collision: {collision_reason}")


def _render_copyable_email(email_body: str) -> None:
    """Show a draft and a browser-side Copy button; never send the message."""
    st.text_area("Email draft", value=email_body, height=250, disabled=True)
    safe_body = html.escape(email_body, quote=False)
    component = f"""
        <div style="font-family:system-ui,sans-serif;display:flex;align-items:center;gap:12px">
          <textarea id="copy-source" style="position:absolute;left:-10000px">{safe_body}</textarea>
          <button id="copy-button" style="background:#173b66;color:white;border:0;border-radius:8px;
            padding:9px 16px;font-weight:650;cursor:pointer">Copy email</button>
          <span id="copy-status" style="color:#16804e;font-size:13px"></span>
        </div>
        <script>
          const copyButton = document.getElementById('copy-button');
          copyButton.addEventListener('click', async () => {{
            const source = document.getElementById('copy-source');
            const status = document.getElementById('copy-status');
            try {{
              await navigator.clipboard.writeText(source.value);
            }} catch (error) {{
              source.style.position = 'fixed'; source.style.left = '0';
              source.select(); document.execCommand('copy');
              source.style.left = '-10000px';
            }}
            status.textContent = 'Copied';
          }});
        </script>
    """
    st.components.v1.html(component, height=48, scrolling=False)


def _live_run_page() -> None:
    st.markdown('<div class="brand-kicker">Vendor operations</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="hero-title">Live Run</h1>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-copy">Validate a vendor submission and follow every check as it runs.</div>',
        unsafe_allow_html=True,
    )

    if "stage_state" not in st.session_state:
        st.session_state.stage_state = _initial_stage_state()
    if "last_decision" not in st.session_state:
        st.session_state.last_decision = None
    if "last_email_draft" not in st.session_state:
        st.session_state.last_email_draft = None

    submission, run_clicked = _render_submission_inputs()

    st.subheader("Pipeline activity")
    slots = []
    for index, _ in enumerate(STAGE_NAMES):
        slot = st.empty()
        slots.append(slot)
        _render_stage(slot, index, st.session_state.stage_state[index])

    if run_clicked:
        if submission is None:
            st.warning("Select or provide a valid JSON submission before starting the run.")
            return

        st.session_state.stage_state = _initial_stage_state()
        st.session_state.last_decision = None
        st.session_state.last_email_draft = None
        for index, slot in enumerate(slots):
            _render_stage(slot, index, st.session_state.stage_state[index])

        active_stage_name = "database intake"
        try:
            run_id = create_run(submission, DEFAULT_DB_PATH)
            run_record = get_run(run_id, DEFAULT_DB_PATH)
            previous_run_id = run_record.get("linked_previous_run_id") if run_record else None
            previous_run = get_run(previous_run_id, DEFAULT_DB_PATH) if previous_run_id else None
            changes = (
                describe_resubmission_changes(previous_run, submission)
                if previous_run else None
            )
            intake_result = CheckResult(
                stage="Intake",
                check_name="submission_received",
                status="pass",
                reason=(
                    f"Submission for {submission.company_name} in {submission.country} was parsed from JSON. "
                    f"It is linked to previous run {previous_run_id}."
                    if previous_run_id
                    else f"Submission for {submission.company_name} in {submission.country} was parsed from JSON."
                ),
            )
            active_stage_name = "intake"
            _record_simple_stage(run_id, slots, 0, intake_result, DEFAULT_DB_PATH)
            if changes:
                _record_simple_stage(run_id, slots, 0, changes, DEFAULT_DB_PATH)

            active_stage_name = "document reading"
            _set_stage(slots, 1, "running")
            with st.spinner(
                "Reading attached documents with Gemini. Larger PDFs can take longer."
            ):
                reading = read_documents(submission)
            for document_check in reading.checks:
                record_event(run_id, document_check, DEFAULT_DB_PATH)
                _add_check(1, document_check)
                _set_stage(slots, 1, "running")
            _set_stage(
                slots,
                1,
                _status_from_checks(st.session_state.stage_state[1]["checks"]),
            )
            time.sleep(STAGE_DELAY_SECONDS)

            active_index: int | None = None

            def on_pipeline_event(event_run_id: str, result: CheckResult) -> None:
                nonlocal active_index, active_stage_name
                active_stage_name = result.stage
                index = STAGE_INDEX[result.stage]
                if active_index is not None and index != active_index:
                    previous_state = st.session_state.stage_state[active_index]
                    _set_stage(
                        slots,
                        active_index,
                        _status_from_checks(previous_state["checks"]),
                    )
                    time.sleep(STAGE_DELAY_SECONDS)
                if index != active_index:
                    active_index = index
                    _set_stage(slots, index, "running")
                    time.sleep(0.12)
                _add_check(index, result)
                if result.stage == "Decision":
                    _set_stage(slots, index, _status_from_checks(st.session_state.stage_state[index]["checks"]))
                else:
                    _set_stage(slots, index, "running")

            decision, pipeline_results = run_pipeline(
                submission,
                event_callback=on_pipeline_event,
                db_path=DEFAULT_DB_PATH,
                run_id=run_id,
                prior_results=reading.checks,
            )

            active_stage_name = "communication"
            time.sleep(STAGE_DELAY_SECONDS)
            email_draft = draft_vendor_email(submission, decision.status, pipeline_results)
            if email_draft:
                save_drafted_email(run_id, email_draft.body, DEFAULT_DB_PATH)
                st.session_state.last_email_draft = email_draft.body
                communication_status = "pass" if email_draft.source == "Gemini" else "warn"
                cache_note = " The response came from cache." if email_draft.from_cache else ""
                communication_reason = (
                    f"Created a vendor email draft with {email_draft.source}.{cache_note} "
                    "The email was saved for review and was not sent."
                )
            else:
                communication_status = "pass"
                communication_reason = "No vendor email is needed for an approved submission."
            communication_result = CheckResult(
                stage="Communication",
                check_name="vendor_communication",
                status=communication_status,
                reason=communication_reason,
            )
            _record_simple_stage(run_id, slots, 7, communication_result, DEFAULT_DB_PATH)

            active_stage_name = "audit logging"
            audit_result = CheckResult(
                stage="Audit log",
                check_name="run_audit_trail",
                status="pass",
                reason="Run details and pipeline check results were written to the audit database.",
            )
            _set_stage(slots, 8, "running")
            time.sleep(0.12)
            record_event(run_id, audit_result, DEFAULT_DB_PATH)
            _add_check(8, audit_result)
            _set_stage(slots, 8, "passed")

            st.session_state.last_decision = decision
            st.session_state.last_run_id = run_id
        except sqlite3.Error:
            LOGGER.exception("Database error during %s", active_stage_name)
            st.error("The run could not be saved because the local database is unavailable. Check write access to the data folder, then retry.")
            return
        except Exception as error:
            LOGGER.exception("Vendor run failed during %s", active_stage_name)
            st.error(
                f"The run could not complete during {active_stage_name}. Check the submission values and referenced files, then retry. "
                f"({type(error).__name__})"
            )
            return

    if st.session_state.last_decision is not None:
        _render_decision(st.session_state.last_decision)
        if st.session_state.last_email_draft:
            st.subheader("Vendor email draft")
            st.caption("Draft only. Nothing has been sent.")
            _render_copyable_email(st.session_state.last_email_draft)


def _history_page() -> None:
    st.markdown('<div class="brand-kicker">Vendor operations</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="hero-title">Run History</h1>', unsafe_allow_html=True)
    st.markdown('<div class="hero-copy">Recent decisions and their audit trail.</div>', unsafe_allow_html=True)
    runs = list_runs(DEFAULT_DB_PATH)
    if not runs:
        st.info("No onboarding runs have been recorded yet.")
        return
    for run in runs:
        status = str(run["status"])
        with st.container(border=True):
            left, right = st.columns([0.72, 0.28], vertical_alignment="center")
            with left:
                st.markdown(f"**{run['vendor_name']}** · {run['country']}")
                st.caption(f"{run['created_at']} · Run {run['id']}")
            with right:
                st.write(status)
            if run["linked_previous_run_id"]:
                st.caption(f"Linked to previous run {run['linked_previous_run_id']}")
            reasons = run["decision_reasons"]
            if reasons:
                with st.expander("Decision reasons"):
                    for reason in reasons:
                        st.write(f"• {reason}")


def _status_cell_style(status: str) -> str:
    return {
        "APPROVED": "background-color: #e1f5eb; color: #087443; font-weight: 700",
        "PENDING": "background-color: #fff1d1; color: #9a5b00; font-weight: 700",
        "REJECTED": "background-color: #fde7e9; color: #a32b36; font-weight: 700",
        "IN_PROGRESS": "background-color: #e5efff; color: #2463c7; font-weight: 700",
    }.get(status, "")


def _dashboard_page() -> None:
    st.markdown('<div class="brand-kicker">Vendor operations</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="hero-title">Dashboard</h1>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-copy">Portfolio health, recent decisions, and a complete audit trail.</div>',
        unsafe_allow_html=True,
    )

    runs = list_runs(DEFAULT_DB_PATH, limit=None)
    status_counts = {
        status: sum(run["status"] == status for run in runs)
        for status in ("APPROVED", "PENDING", "REJECTED")
    }
    metrics = st.columns(4)
    metric_values = [
        ("Total runs", len(runs)),
        ("Approved", status_counts["APPROVED"]),
        ("Pending", status_counts["PENDING"]),
        ("Rejected", status_counts["REJECTED"]),
    ]
    for column, (label, value) in zip(metrics, metric_values):
        with column:
            with st.container(border=True):
                st.metric(label, value)

    with st.container(border=True):
        reset_col, copy_col = st.columns([0.35, 0.65], vertical_alignment="center")
        with reset_col:
            clear_clicked = st.button("Clear demo data and reseed", use_container_width=True)
        with copy_col:
            st.caption("This removes saved runs and events, then restores the example vendor list.")
    if clear_clicked:
        reset_demo_data(DEFAULT_DB_PATH)
        st.session_state.pop("dashboard_selected_run", None)
        st.success("Demo history cleared and example vendors reseeded.")
        st.rerun()

    st.subheader("Runs")
    status_options = ["All statuses", "APPROVED", "PENDING", "REJECTED", "IN_PROGRESS"]
    countries = sorted({str(run["country"]) for run in runs})
    filter_cols = st.columns([0.24, 0.24, 0.52])
    with filter_cols[0]:
        selected_status = st.selectbox("Status", status_options)
    with filter_cols[1]:
        selected_country = st.selectbox("Country", ["All countries", *countries])
    with filter_cols[2]:
        search_name = st.text_input("Search vendor name", placeholder="Type a vendor name")

    visible_runs = [
        run for run in runs
        if (selected_status == "All statuses" or run["status"] == selected_status)
        and (selected_country == "All countries" or run["country"] == selected_country)
        and search_name.casefold() in str(run["vendor_name"]).casefold()
    ]
    table_rows = []
    for run in visible_runs:
        reasons = run["decision_reasons"]
        table_rows.append(
            {
                "Run ID": run["id"],
                "Vendor name": run["vendor_name"],
                "Country": run["country"],
                "Status": run["status"],
                "Date": str(run["created_at"]).replace("T", " ").split(".")[0],
                "Top reason": reasons[0] if reasons else "No decision reason saved yet.",
            }
        )
    if table_rows:
        table = pd.DataFrame(table_rows)
        styled_table = table.style.applymap(_status_cell_style, subset=["Status"])
        st.dataframe(styled_table, use_container_width=True, hide_index=True)
        run_by_id = {str(run["id"]): run for run in visible_runs}
        run_options = [str(run["id"]) for run in visible_runs]
        if st.session_state.get("dashboard_selected_run") not in run_options:
            st.session_state.pop("dashboard_selected_run", None)
        selected_run_id = st.selectbox(
            "Inspect a run",
            options=run_options,
            format_func=lambda run_id: (
                f"{run_by_id[run_id]['vendor_name']} · {run_by_id[run_id]['country']} · "
                f"{run_by_id[run_id]['status']} · {run_id[:8]}"
            ),
            key="dashboard_selected_run",
        )
        selected_run = get_run(selected_run_id, DEFAULT_DB_PATH)
        if selected_run:
            st.subheader("Run details")
            linked_runs = get_linked_runs(selected_run_id, DEFAULT_DB_PATH)
            if len(linked_runs) > 1:
                st.markdown("#### Linked resubmission runs")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Run ID": run["id"],
                                "Vendor name": run["vendor_name"],
                                "Status": run["status"],
                                "Date": str(run["created_at"]).replace("T", " ").split(".")[0],
                                "Previous run": run["linked_previous_run_id"] or "—",
                            }
                            for run in linked_runs
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            detail_col, submission_col = st.columns([0.55, 0.45])
            with detail_col:
                st.markdown(f"**{selected_run['vendor_name']}** · {selected_run['country']}")
                st.caption(f"Run ID: {selected_run['id']} · {selected_run['created_at']}")
                st.markdown(f"**Status:** {selected_run['status']}")
                st.markdown("#### Audit trail")
                events = get_run_events(selected_run_id, DEFAULT_DB_PATH)
                collision_event = next(
                    (
                        event for event in events
                        if event["check_name"] == "bank_account_reuse"
                        and event["status"] == "fail"
                    ),
                    None,
                )
                if collision_event:
                    st.error(f"Bank account collision: {collision_event['reason']}")
                if not events:
                    st.info("This run has no saved check events.")
                for position, event in enumerate(events, start=1):
                    with st.container(border=True):
                        st.markdown(
                            f"**{position:02d}. {event['stage']}** · {event['check_name']} · "
                            f"{str(event['status']).upper()}"
                        )
                        st.caption(event["timestamp"])
                        st.write(event["reason"])
            with submission_col:
                st.markdown("#### Original submission")
                st.json(selected_run["submission_json"])
                st.markdown("#### Drafted email")
                drafted_email = selected_run.get("drafted_email")
                if drafted_email:
                    _render_copyable_email(drafted_email)
                else:
                    st.info("No email draft is saved for this run.")
    else:
        st.info("No runs match these filters." if runs else "No runs recorded yet. Start a Live Run to populate the dashboard.")


def main() -> None:
    st.set_page_config(page_title="Vendor Onboarding", page_icon="✓", layout="wide")
    _load_gemini_key()
    try:
        initialize_database(DEFAULT_DB_PATH)
    except (sqlite3.Error, OSError, ValueError):
        LOGGER.exception("Could not initialize the vendor onboarding database")
        st.error("The app could not initialize its database. Check that the data folder is writable, then reload the app.")
        st.stop()
    _inject_styles()
    with st.sidebar:
        st.markdown("## Vendor onboarding")
        st.caption("Deterministic validation workspace")
        page = st.radio(
            "Workspace", ["Dashboard", "Live Run", "Run History"], label_visibility="collapsed"
        )
        st.divider()
        st.caption("Every decision is recorded with its checks and reasons.")

    if page == "Dashboard":
        _dashboard_page()
    elif page == "Live Run":
        _live_run_page()
    else:
        _history_page()


if __name__ == "__main__":
    main()
