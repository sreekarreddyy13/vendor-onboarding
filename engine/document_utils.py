"""Helpers for interpreting document names supplied with a submission."""

from collections.abc import Mapping


def provided_document_names(
    document_files: Mapping[str, str] | None,
) -> set[str]:
    """Return document types that have a file reference in the submission."""
    return {str(name).strip().casefold() for name in (document_files or {})}
