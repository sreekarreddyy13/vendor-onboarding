import json
from pathlib import Path

from app.db import get_existing_vendors, get_run, get_run_events
from engine.models import CheckResult, VendorSubmission
from engine.validation import run_pipeline


SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def load_happy_submission() -> VendorSubmission:
    with (SAMPLES / "happy.json").open("r", encoding="utf-8") as sample_file:
        return VendorSubmission(**json.load(sample_file))


def test_existing_vendors_are_seeded_from_json(tmp_path: Path) -> None:
    database = tmp_path / "onboarding.sqlite3"

    first_load = get_existing_vendors(database)
    second_load = get_existing_vendors(database)

    assert len(first_load) == 6
    assert first_load == second_load
    assert first_load[0]["status"] == "approved"


def test_pipeline_persists_and_emits_each_result(tmp_path: Path) -> None:
    database = tmp_path / "onboarding.sqlite3"
    emitted: list[tuple[str, str]] = []

    def on_event(run_id: str, result: CheckResult) -> None:
        # Looking up events here confirms persistence happens before notification.
        events = get_run_events(run_id, database)
        assert events[-1]["check_name"] == result.check_name
        emitted.append((run_id, result.check_name))

    decision, results = run_pipeline(
        load_happy_submission(),
        vendors=[],
        event_callback=on_event,
        db_path=database,
    )

    stored_run = get_run(decision.run_id, database)
    stored_events = get_run_events(decision.run_id, database)
    assert stored_run is not None
    assert stored_run["status"] == decision.status
    assert stored_run["decision_reasons"] == decision.reasons
    assert [event["check_name"] for event in stored_events] == [
        result.check_name for result in results
    ]
    assert [name for _, name in emitted] == [result.check_name for result in results]


def test_runs_have_unique_ids_and_link_prior_same_name_run(tmp_path: Path) -> None:
    database = tmp_path / "onboarding.sqlite3"

    first, _ = run_pipeline(load_happy_submission(), vendors=[], db_path=database)
    second, _ = run_pipeline(load_happy_submission(), vendors=[], db_path=database)

    assert first.run_id != second.run_id
    assert get_run(second.run_id, database)["linked_previous_run_id"] == first.run_id
