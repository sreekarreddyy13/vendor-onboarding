"""Tests for document extraction using a fake LLM response."""

import json

from engine.extract import DOCUMENT_SCHEMA, read_documents
from engine.llm import LLMError, LLMResult
from engine.models import VendorSubmission


def _submission(tmp_path, **kwargs):
    document = tmp_path / "tax.pdf"
    document.write_bytes(b"fake PDF bytes")
    values = {
        "company_name": "Northstar Foods Private Limited",
        "country": "India",
        "tax_id": "27AABCU9603R1ZM",
        "bank_account_number": "1234567890",
        "bank_code": "HDFC0001234",
        "bank_account_holder_name": "Northstar Foods Pvt Ltd",
        "document_files": {"gst_registration": str(document)},
    }
    values.update(kwargs)
    return VendorSubmission(**values)


def _payload(**updates):
    data = {
        "company_name": "Northstar Foods Pvt Ltd",
        "tax_id": "27AABCU9603R1ZM",
        "account_holder_name": "Northstar Foods Pvt Ltd",
        "account_number": "1234567890",
        "bank_code": "HDFC0001234",
        "document_type": "gst_registration",
    }
    data.update(updates)
    return data


def test_extracts_document_and_marks_cached_response(tmp_path):
    calls = []

    def fake_llm(path, prompt, schema):
        calls.append((path, prompt, schema))
        return LLMResult(json.dumps(_payload()), from_cache=True)

    result = read_documents(_submission(tmp_path), llm_function=fake_llm)

    assert len(calls) == 1
    assert calls[0][2] == DOCUMENT_SCHEMA
    assert result.documents[0].extracted["tax_id"] == "27AABCU9603R1ZM"
    assert result.documents[0].from_cache is True
    assert any("from cache" in check.reason for check in result.checks)


def test_disagreement_is_a_warning(tmp_path):
    def fake_llm(path, prompt, schema):
        return LLMResult(json.dumps(_payload(tax_id="29AABCU9603R1ZM")))

    result = read_documents(_submission(tmp_path), llm_function=fake_llm)

    assert any(check.status == "warn" and "tax id" in check.reason for check in result.checks)


def test_malformed_json_retries_once_then_warns(tmp_path):
    calls = 0

    def fake_llm(path, prompt, schema):
        nonlocal calls
        calls += 1
        return LLMResult("not valid json")

    result = read_documents(_submission(tmp_path), llm_function=fake_llm)

    assert calls == 2
    assert result.documents[0].extracted == {}
    assert any(check.status == "warn" and "could not be validated" in check.reason for check in result.checks)
    assert result.form_values["tax_id"] == "27AABCU9603R1ZM"


def test_llm_failure_skips_extraction_and_keeps_form_values(tmp_path):
    def fake_llm(path, prompt, schema):
        raise LLMError("test failure")

    result = read_documents(_submission(tmp_path), llm_function=fake_llm)

    assert any(check.status == "warn" and "extraction was skipped" in check.reason for check in result.checks)
    assert result.form_values["company_name"] == "Northstar Foods Private Limited"
    assert result.documents[0].extracted == {}


def test_missing_document_file_is_a_clear_warning(tmp_path):
    submission = _submission(tmp_path, document_files={"gst_registration": str(tmp_path / "missing.pdf")})

    result = read_documents(submission)

    assert result.documents[0].extracted == {}
    assert any("was not found" in check.reason for check in result.checks)
