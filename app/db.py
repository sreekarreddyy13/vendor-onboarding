"""SQLite persistence for validation runs, events, and reference vendors."""

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.config import PROJECT_ROOT
from engine.models import CheckResult, VendorSubmission


DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "vendor_onboarding.sqlite3"
DEFAULT_VENDOR_JSON = PROJECT_ROOT / "data" / "existing_vendors.json"


def _connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection with foreign key enforcement enabled."""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(
    db_path: str | Path = DEFAULT_DB_PATH,
    vendor_json_path: str | Path = DEFAULT_VENDOR_JSON,
) -> None:
    """Create the application tables and seed reference vendors on first use."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(db_path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                vendor_name TEXT NOT NULL,
                country TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                submission_json TEXT NOT NULL,
                decision_reasons TEXT NOT NULL,
                linked_previous_run_id TEXT,
                drafted_email TEXT,
                FOREIGN KEY (linked_previous_run_id) REFERENCES runs(id)
            );

            CREATE TABLE IF NOT EXISTS run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                check_name TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            );

            CREATE TABLE IF NOT EXISTS existing_vendors (
                vendor_id TEXT PRIMARY KEY,
                company_name TEXT NOT NULL,
                country TEXT NOT NULL,
                tax_id TEXT NOT NULL,
                bank_account_number TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        run_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "drafted_email" not in run_columns:
            connection.execute("ALTER TABLE runs ADD COLUMN drafted_email TEXT")
        count = connection.execute("SELECT COUNT(*) FROM existing_vendors").fetchone()[0]
        if count == 0:
            with Path(vendor_json_path).open("r", encoding="utf-8") as vendor_file:
                vendors = json.load(vendor_file)
            connection.executemany(
                """INSERT INTO existing_vendors
                   (vendor_id, company_name, country, tax_id, bank_account_number, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        str(vendor["vendor_id"]),
                        str(vendor["company_name"]),
                        str(vendor["country"]),
                        str(vendor["tax_id"]),
                        str(vendor["bank_account_number"]),
                        str(vendor.get("status", "approved")),
                    )
                    for vendor in vendors
                ],
            )


def get_existing_vendors(
    db_path: str | Path = DEFAULT_DB_PATH,
    vendor_json_path: str | Path = DEFAULT_VENDOR_JSON,
) -> list[dict[str, Any]]:
    """Load the reference vendor set from SQLite, seeding it from JSON once."""
    initialize_database(db_path, vendor_json_path)
    with closing(_connect(db_path)) as connection, connection:
        rows = connection.execute(
            """SELECT vendor_id, company_name, country, tax_id,
                      bank_account_number, status
               FROM existing_vendors ORDER BY vendor_id"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_historical_vendor_records(
    db_path: str | Path = DEFAULT_DB_PATH,
    exclude_run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load vendor identities and bank accounts from earlier completed runs."""
    initialize_database(db_path)
    query = """SELECT id, vendor_name, status, submission_json
               FROM runs WHERE status IN ('APPROVED', 'PENDING', 'REJECTED')"""
    params: tuple[Any, ...] = ()
    if exclude_run_id:
        query += " AND id != ?"
        params = (exclude_run_id,)
    query += " ORDER BY created_at"
    with closing(_connect(db_path)) as connection, connection:
        rows = connection.execute(query, params).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        submission = json.loads(row["submission_json"])
        records.append(
            {
                "vendor_id": f"previous run {row['id']}",
                "company_name": submission.get("company_name") or row["vendor_name"],
                "country": submission.get("country", ""),
                "tax_id": submission.get("tax_id", ""),
                "bank_account_number": submission.get("bank_account_number", ""),
                "status": row["status"],
            }
        )
    return records


def create_run(
    submission: VendorSubmission,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> str:
    """Create a unique run record and link it to the latest same-name run."""
    initialize_database(db_path)
    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(submission.__dict__, ensure_ascii=False, sort_keys=True)
    with closing(_connect(db_path)) as connection, connection:
        # Prefer the latest pending attempt with the same tax ID, even when the
        # vendor has corrected its company name spelling on the resubmission.
        previous_id = None
        normalized_tax_id = submission.tax_id.strip().casefold()
        if normalized_tax_id:
            pending_runs = connection.execute(
                """SELECT id, submission_json FROM runs
                   WHERE status = 'PENDING' ORDER BY created_at DESC"""
            ).fetchall()
            for pending_run in pending_runs:
                prior_payload = json.loads(pending_run["submission_json"])
                prior_tax_id = str(prior_payload.get("tax_id", "")).strip().casefold()
                if prior_tax_id == normalized_tax_id:
                    previous_id = pending_run["id"]
                    break

        # Keep ordinary same-name run history linked if there is no pending
        # tax-ID match to treat as a correction/resubmission.
        previous = connection.execute(
            """SELECT id FROM runs
               WHERE lower(trim(vendor_name)) = lower(trim(?))
               ORDER BY created_at DESC LIMIT 1""",
            (submission.company_name,),
        ).fetchone()
        if previous_id is None and previous:
            previous_id = previous["id"]
        connection.execute(
            """INSERT INTO runs
               (id, vendor_name, country, status, created_at, submission_json,
                decision_reasons, linked_previous_run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                submission.company_name,
                submission.country,
                "IN_PROGRESS",
                created_at,
                payload,
                json.dumps([]),
                previous_id,
            ),
        )
    return run_id


def get_linked_runs(
    run_id: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Return the whole parent/child resubmission chain containing a run."""
    initialize_database(db_path)
    with closing(_connect(db_path)) as connection, connection:
        rows = connection.execute(
            """SELECT id, vendor_name, country, status, created_at,
                      linked_previous_run_id
               FROM runs ORDER BY created_at"""
        ).fetchall()
    runs = [dict(row) for row in rows]
    by_id = {str(run["id"]): run for run in runs}
    if run_id not in by_id:
        return []

    # Walk to the oldest parent first, protecting against corrupt cyclic links.
    root_id = run_id
    seen: set[str] = set()
    while root_id not in seen:
        seen.add(root_id)
        parent_id = by_id[root_id].get("linked_previous_run_id")
        if not parent_id or parent_id not in by_id:
            break
        root_id = str(parent_id)

    chain = [by_id[root_id]]
    included = {root_id}
    while True:
        children = [
            run for run in runs
            if run.get("linked_previous_run_id") in included
            and str(run["id"]) not in included
        ]
        if not children:
            break
        for child in children:
            chain.append(child)
            included.add(str(child["id"]))
    return chain


def record_event(
    run_id: str,
    result: CheckResult,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Persist one check result as an append-only run event."""
    timestamp = datetime.now(timezone.utc).isoformat()
    with closing(_connect(db_path)) as connection, connection:
        connection.execute(
            """INSERT INTO run_events
               (run_id, stage, check_name, status, reason, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, result.stage, result.check_name, result.status, result.reason, timestamp),
        )


def complete_run(
    run_id: str,
    status: str,
    reasons: list[str],
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Write the final status and plain-English decision reasons to the run."""
    with closing(_connect(db_path)) as connection, connection:
        connection.execute(
            "UPDATE runs SET status = ?, decision_reasons = ? WHERE id = ?",
            (status, json.dumps(reasons, ensure_ascii=False), run_id),
        )


def save_drafted_email(
    run_id: str,
    drafted_email: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Attach a generated vendor email to its run when one is available."""
    with closing(_connect(db_path)) as connection, connection:
        connection.execute(
            "UPDATE runs SET drafted_email = ? WHERE id = ?",
            (drafted_email, run_id),
        )


def get_run_events(run_id: str, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Return stored events in the order they were written."""
    with closing(_connect(db_path)) as connection, connection:
        rows = connection.execute(
            """SELECT id, run_id, stage, check_name, status, reason, timestamp
               FROM run_events WHERE run_id = ? ORDER BY id""",
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_run(run_id: str, db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    """Return one run record, decoding its JSON fields for callers."""
    with closing(_connect(db_path)) as connection, connection:
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["submission_json"] = json.loads(run["submission_json"])
    run["decision_reasons"] = json.loads(run["decision_reasons"])
    return run


def list_runs(
    db_path: str | Path = DEFAULT_DB_PATH,
    limit: int | None = 50,
) -> list[dict[str, Any]]:
    """Return the newest runs for the history page."""
    initialize_database(db_path)
    with closing(_connect(db_path)) as connection, connection:
        query = """SELECT id, vendor_name, country, status, created_at,
                          decision_reasons, linked_previous_run_id
                   FROM runs ORDER BY created_at DESC"""
        if limit is not None:
            rows = connection.execute(query + " LIMIT ?", (limit,)).fetchall()
        else:
            rows = connection.execute(query).fetchall()
    runs = [dict(row) for row in rows]
    for run in runs:
        run["decision_reasons"] = json.loads(run["decision_reasons"])
    return runs


def reset_demo_data(
    db_path: str | Path = DEFAULT_DB_PATH,
    vendor_json_path: str | Path = DEFAULT_VENDOR_JSON,
) -> None:
    """Delete saved runs and restore reference vendors from the JSON seed."""
    with Path(vendor_json_path).open("r", encoding="utf-8") as vendor_file:
        seed_vendors = json.load(vendor_file)
    initialize_database(db_path, vendor_json_path)
    with closing(_connect(db_path)) as connection, connection:
        connection.execute("DELETE FROM run_events")
        connection.execute("DELETE FROM runs")
        connection.execute("DELETE FROM existing_vendors")
        connection.executemany(
            """INSERT INTO existing_vendors
               (vendor_id, company_name, country, tax_id, bank_account_number, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    str(vendor["vendor_id"]),
                    str(vendor["company_name"]),
                    str(vendor["country"]),
                    str(vendor["tax_id"]),
                    str(vendor["bank_account_number"]),
                    str(vendor.get("status", "approved")),
                )
                for vendor in seed_vendors
            ],
        )
