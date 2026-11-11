"""Small Gemini client wrapper with bounded retries and a local response cache."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.config import PROJECT_ROOT, load_config


class LLMError(RuntimeError):
    """Raised when the Gemini request cannot be completed."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    from_cache: bool = False


def _api_key() -> str | None:
    """Read the key at call time so both Streamlit and CLI use are supported."""
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key
    try:
        import streamlit as st

        key = st.secrets.get("GEMINI_API_KEY")
        return str(key) if key else None
    except Exception:
        # Streamlit is optional for CLI usage and raises if no secrets are set.
        return None


def _model_settings() -> tuple[dict[str, Any], str]:
    ai_config = load_config().get("ai", {})
    model = str(ai_config.get("model_name", "gemini-2.5-flash"))
    return ai_config, model


def _cache_path(cache_material: bytes, prompt: str, model: str, ai_config: dict[str, Any]) -> Path:
    digest = hashlib.sha256()
    digest.update(cache_material)
    digest.update(b"\0")
    digest.update(prompt.encode("utf-8"))
    digest.update(b"\0")
    digest.update(model.encode("utf-8"))
    cache_dir = PROJECT_ROOT / str(ai_config.get("cache_dir", "cache"))
    return cache_dir / f"{digest.hexdigest()}.json"


def _read_cache(path: Path) -> LLMResult | None:
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and isinstance(cached.get("text"), str):
            return LLMResult(text=cached["text"], from_cache=True)
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _write_cache(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _status_code(error: Exception) -> int | None:
    for name in ("status_code", "code"):
        value = getattr(error, name, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _retryable(error: Exception) -> bool:
    status = _status_code(error)
    if status == 429 or status == 408 or (status is not None and 500 <= status <= 599):
        return True
    name = type(error).__name__.casefold()
    return any(token in name for token in ("timeout", "connection", "temporar"))


def _generate(prompt: str, *, document_bytes: bytes | None = None,
              mime_type: str | None = None, schema: dict[str, Any] | None = None,
              cache_material: bytes = b"") -> LLMResult:
    ai_config, model = _model_settings()
    cache_path = _cache_path(cache_material, prompt, model, ai_config)
    cached = _read_cache(cache_path)
    if cached:
        return cached
    # Preserve already-warmed demo outputs when the configured live model is
    # upgraded. Each compatibility entry still uses its own model-specific key.
    for cached_model in ai_config.get("cache_compatibility_models", []):
        compatible_path = _cache_path(
            cache_material, prompt, str(cached_model), ai_config
        )
        cached = _read_cache(compatible_path)
        if cached:
            return cached

    api_key = _api_key()
    if not api_key:
        raise LLMError("GEMINI_API_KEY is not set in the environment or Streamlit secrets.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise LLMError("Gemini support is unavailable; install the google-genai package.") from error

    timeout_ms = int(float(ai_config.get("timeout_seconds", 45)) * 1000)
    max_attempts = max(1, int(ai_config.get("max_attempts", 3)))
    retry_base = float(ai_config.get("retry_base_seconds", 2))
    retry_cap = float(ai_config.get("retry_max_seconds", 8))
    # Disable SDK-level status retries where supported so this wrapper owns the
    # retry limit and backoff. The timeout is applied to each network request.
    try:
        http_options = types.HttpOptions(
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1, http_status_codes=[]),
        )
    except Exception:
        http_options = types.HttpOptions(timeout=timeout_ms)

    try:
        client = genai.Client(api_key=api_key, http_options=http_options)
    except Exception as error:
        raise LLMError(f"Could not initialize the Gemini client ({type(error).__name__}).") from error
    contents: list[Any] = [prompt]
    if document_bytes is not None:
        contents.append(types.Part.from_bytes(data=document_bytes, mime_type=mime_type or "application/octet-stream"))
    request_config: dict[str, Any] = {}
    if schema is not None:
        request_config.update(response_mime_type="application/json", response_schema=schema)

    last_error: Exception | None = None
    try:
        for attempt in range(max_attempts):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(**request_config) if request_config else None,
                )
                response_text = getattr(response, "text", None)
                if not isinstance(response_text, str) or not response_text.strip():
                    raise LLMError("Gemini returned an empty response.")
                try:
                    _write_cache(cache_path, response_text)
                except OSError:
                    # A cache write problem should not discard a valid response.
                    pass
                return LLMResult(text=response_text, from_cache=False)
            except Exception as error:
                last_error = error
                if attempt + 1 >= max_attempts or not _retryable(error):
                    break
                time.sleep(min(retry_base * (2 ** attempt), retry_cap))
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Cleanup errors should not hide the model response or its error.
                pass
    # Keep provider exception text out of the audit/UI because it can contain
    # request details. The exception class and status are enough to diagnose it.
    status = _status_code(last_error) if last_error else None
    detail = f" (HTTP {status})" if status else ""
    error_name = type(last_error).__name__ if last_error else "unknown error"
    raise LLMError(
        f"Gemini request failed after {min(max_attempts, attempt + 1)} attempt(s) ({error_name}){detail}."
    ) from last_error


def generate_text(prompt: str) -> LLMResult:
    """Generate text for approved AI use cases, such as communication drafts."""
    return _generate(prompt, cache_material=b"")


def extract_json_from_document(file_path: str | Path, prompt: str,
                               schema: dict[str, Any]) -> LLMResult:
    """Send a PDF or image as bytes and request a structured JSON response."""
    path = Path(file_path)
    try:
        file_bytes = path.read_bytes()
    except OSError as error:
        raise LLMError(f"Could not read document file: {path}") from error
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type not in {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise LLMError(f"Unsupported document type for extraction: {path.suffix or 'unknown'}")
    return _generate(
        prompt,
        document_bytes=file_bytes,
        mime_type=mime_type,
        schema=schema,
        cache_material=file_bytes,
    )
