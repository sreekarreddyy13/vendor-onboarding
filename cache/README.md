# Bundled sample extraction cache

These entries are precomputed structured fixtures for the fictional PDFs and scan in `data/documents/`. Their filenames use the same SHA-256 key as `engine/llm.py` (document bytes, extraction prompt, and configured model), so the sample document-reading demo works offline and does not need a Gemini API call.

New or changed documents/prompts use the normal Gemini flow and can add cache entries at runtime. The fixture values mirror the submitted sample forms, including deliberately invalid IDs and names, so deterministic validation still demonstrates those edge cases.
