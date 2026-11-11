# Project context

PROJECT: An automated vendor onboarding process for a take-home case study. A vendor submission (JSON, optionally with PDF documents) goes in. The process validates it and outputs a status: APPROVED, PENDING, or REJECTED, with visible reasoning. For anything not approved, it drafts an email telling the vendor what is needed. Every stage must be logged to an audit trail.

STACK: Python 3.11+, Streamlit for the UI, SQLite for storage, rapidfuzz for fuzzy name matching, pytest for tests. AI (an LLM API) is used ONLY for reading uploaded documents and drafting emails. ALL validation and decision logic is deterministic plain Python so the same input always gives the same output and every decision can be explained.

PIPELINE STAGES (in order): 1 Intake, 2 Document reading, 3 Completeness check, 4 Format validation, 5 Cross-referencing, 6 Risk checks, 7 Decision, 8 Communication, 9 Audit log.

RULES: Keep all rules and thresholds in config files, not hardcoded. Every check must return a structured result: stage name, check name, passed/failed/warning, and a plain-English reason. Keep code simple and well commented, because the author must explain it in an interview. Never put API keys in code; read them from environment variables. If the AI call fails, the app must degrade gracefully and never crash.

ENVIRONMENT: The existing .venv, .env, and .gitignore belong to the user. Never overwrite .env or .gitignore. Load GEMINI_API_KEY at application startup with python-dotenv (load_dotenv), falling back to normal environment variables and then Streamlit secrets when deployed. Never print or log the real key. Provide only a placeholder in .env.example (GEMINI_API_KEY=your_key_here).
