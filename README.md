# Vendor Onboarding

Vendor Onboarding is a demo app for reviewing vendor applications. It accepts a JSON submission and optional supporting documents, checks the information with deterministic Python rules, and returns **APPROVED**, **PENDING**, or **REJECTED** with plain-English reasons. It drafts a vendor email for non-approved submissions, stores each run and check in SQLite, and never sends email.

AI is used only to read uploaded documents and draft emails. Both uses have cached results and graceful fallbacks. All validation and decision rules are ordinary Python configured in `config/rules.yaml`.

## Pipeline

| Stage | What happens |
| --- | --- |
| 1. Intake | Parse a sample, pasted JSON, or uploaded JSON submission and create a run record. |
| 2. Document reading | Read referenced PDFs/images with Gemini when available; compare extracted values with the form. |
| 3. Completeness | Check the required fields and documents for the claimed country. |
| 4. Format validation | Check the country, tax ID, bank code, and contact email formats. |
| 5. Cross-referencing | Compare company names and confirm the bank country matches the claimed country. |
| 6. Risk checks | Check blocked countries, duplicate tax IDs, and bank account reuse. |
| 7. Decision | Apply the deterministic decision rules below and record reasons. |
| 8. Communication | Draft an email for PENDING or REJECTED runs; the user can copy it, but the app does not send it. |
| 9. Audit log | Store stage results, reasons, and timestamps in SQLite. |

## Decision rules

| Outcome | Rule |
| --- | --- |
| **REJECTED** | The country is on the blocked list; a bank account is already used by a different vendor; the tax ID is invalid and a company-name comparison fails; or multiple country evidence fields conflict (or one conflict is combined with a name mismatch). |
| **PENDING** | Required information/documents are missing, a non-fatal check fails, any warning is raised, or exactly one country evidence field conflicts with the claimed country. The email asks for the missing item or correction. |
| **APPROVED** | Every configured check passes. |

Name similarity uses the approval and pending thresholds in `config/rules.yaml`. Legal suffixes and punctuation are normalized before comparison; word order is ignored. Bank-account ownership collisions are never exposed in vendor-facing email drafts.

## Run locally

Python 3.11 or later is required. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Add your Gemini key to `.env` if you want live document extraction or email drafting. The key can also be provided through `GEMINI_API_KEY` in the environment. Start the app with:

```powershell
streamlit run app/main.py
```

The database initializes itself at `data/vendor_onboarding.sqlite3` on first launch. The Live Run page includes ordered demo buttons; the sample dropdown also provides all JSON samples. Run the tests with:

```powershell
python -m pytest --basetemp .pytest-run
```

## Recording the demo

From the project root, run the preflight first. It reseeds the local demo database, checks sample files and expected pipeline outcomes, and reports Gemini cache readiness without displaying the API key. Then start Streamlit:

```powershell
.\.venv\Scripts\python.exe scripts\preflight.py
.\.venv\Scripts\python.exe -m streamlit run app\main.py
```

Regenerate the phone-friendly narration guide whenever its timing or content changes:

```powershell
.\.venv\Scripts\python.exe scripts\make_demo_script.py
```

This creates `DEMO_SCRIPT.pdf` in the project root. If preflight reports uncached sample documents, configure `GEMINI_API_KEY` and run `scripts\warm_cache.py` before recording.

## Streamlit Community Cloud

Push the project folder to GitHub and select `app/main.py` as the entrypoint if this folder is the repository root. If the project remains inside a parent repository, select `vendor-onboarding/app/main.py`; `app/requirements.txt` is beside that entrypoint for Community Cloud's dependency discovery. Use Python 3.11 or later in deployment settings. Add `GEMINI_API_KEY` under the app's **Settings → Secrets** as TOML, for example:

```toml
GEMINI_API_KEY = "your_key_here"
```

Without a key, the sample PDFs use the bundled offline extraction fixtures in `cache/`; new documents use form values and a warning when extraction is unavailable, and email drafts use a plain template. Streamlit Community Cloud's local filesystem is ephemeral, so demo run history can reset when the app is restarted.

## Assumptions and limitations

- The country list, formats, blocked-country list, and thresholds are illustrative case-study rules, not legal, tax, or banking advice.
- The included vendors, IDs, account numbers, PDFs, and emails are synthetic demonstration data.
- Regex checks verify shape only; they do not confirm an identifier or bank account with an authority or bank.
- Name similarity can flag a genuine business relationship for review or miss an unusual alias. A human should review uncertain cases.
- Document extraction and email drafting depend on Gemini when no matching cache entry exists. API errors, missing keys, and unreadable files degrade to warnings, submitted form values, and template email drafts.
- SQLite is suitable for this local demo. Community Cloud storage is temporary and is not a production database.
- Email drafts are shown for review and copying only. The app has no email-sending integration.

## Configuration

Rules live in `config/rules.yaml`: country-specific required fields/documents, tax and bank code regexes, the illustrative blocked-country list, name thresholds, suffix variants, and AI model/cache settings. Update the YAML values to change validation policy without embedding thresholds or patterns in decision code.
