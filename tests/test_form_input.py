from types import SimpleNamespace

from app import main as vendor_ui
from engine.config import load_config


def _empty_values() -> dict[str, str]:
    return {
        "form_company_name": "",
        "form_address": "",
        "form_contact_name": "",
        "form_contact_email": "",
        "form_phone": "",
        "form_tax_id": "",
        "form_tax_certificate_name": "",
        "form_bank_account_holder_name": "",
        "form_bank_name": "",
        "form_bank_account_number": "",
        "form_bank_code": "",
        "form_bank_country": "",
    }


def test_form_can_build_an_incomplete_submission() -> None:
    submission = vendor_ui._build_form_submission(
        "",
        _empty_values(),
        {},
        {},
        {},
        load_config(),
    )

    assert submission.company_name == ""
    assert submission.country == ""
    assert submission.tax_id == ""
    assert submission.document_files == {}


def test_document_upload_keys_follow_country_configuration() -> None:
    config = load_config()

    assert vendor_ui._document_keys("India", config) == {
        "tax_certificate": "gst_registration",
        "bank_letter": "bank_letter",
        "incorporation_certificate": "certificate_of_incorporation",
    }
    assert vendor_ui._document_keys("US", config)["tax_certificate"] == "ein_confirmation"
    assert vendor_ui._document_keys("UK", config)["tax_certificate"] == "vat_registration"


def test_unchecked_demo_file_is_removed_but_other_attachments_remain() -> None:
    demo_files = {
        "gst_registration": "data/documents/tax.pdf",
        "bank_letter": "data/documents/bank.pdf",
        "certificate_of_incorporation": "data/documents/incorporation.pdf",
    }
    submission = vendor_ui._build_form_submission(
        "India",
        _empty_values(),
        {},
        {
            "tax_certificate": False,
            "bank_letter": True,
            "incorporation_certificate": True,
        },
        demo_files,
        load_config(),
    )

    assert "gst_registration" not in submission.document_files
    assert "bank_letter" in submission.document_files
    assert "certificate_of_incorporation" in submission.document_files


def test_user_upload_is_saved_with_a_unique_name_under_uploads(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(vendor_ui, "PROJECT_ROOT", tmp_path)
    fake_upload = SimpleNamespace(name="bank letter.PDF", getvalue=lambda: b"sample-pdf")

    first_path = vendor_ui._save_uploaded_file(fake_upload, "bank_letter")
    second_path = vendor_ui._save_uploaded_file(fake_upload, "bank_letter")

    assert first_path.startswith("data/uploads/")
    assert first_path != second_path
    assert (tmp_path / first_path).read_bytes() == b"sample-pdf"

