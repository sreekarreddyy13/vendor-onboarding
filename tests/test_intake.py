import json

import pytest

from engine.intake import parse_submission_json
from engine.models import VendorSubmission


def test_empty_json_has_a_clear_error() -> None:
    with pytest.raises(ValueError, match="Submission JSON is empty"):
        parse_submission_json(" \n")


def test_invalid_json_has_line_and_column() -> None:
    with pytest.raises(ValueError, match="Invalid JSON:.*line 1, column"):
        parse_submission_json('{"company_name":')


@pytest.mark.parametrize("raw_json", ["[]", "null", '"vendor"'])
def test_json_root_must_be_an_object(raw_json: str) -> None:
    with pytest.raises(ValueError, match="root must be an object"):
        parse_submission_json(raw_json)


def test_missing_required_vendor_identity_is_explained() -> None:
    with pytest.raises(ValueError, match="company_name, country"):
        parse_submission_json("{}")


def test_wrong_field_types_are_explained() -> None:
    payload = {"company_name": "Example Co", "country": "India", "documents": "bank_letter"}
    with pytest.raises(ValueError, match="documents must be a list"):
        parse_submission_json(json.dumps(payload))


def test_unknown_country_is_accepted_for_deterministic_validation() -> None:
    submission = parse_submission_json(json.dumps({"company_name": "Example Co", "country": "Mars"}))

    assert isinstance(submission, VendorSubmission)
    assert submission.country == "Mars"
