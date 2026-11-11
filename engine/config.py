"""Load the project's YAML rules file."""

from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "rules.yaml"


def load_config(path: str | Path = DEFAULT_RULES_PATH) -> dict[str, Any]:
    """Return rules from a YAML file, rejecting an empty or non-mapping file."""
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as rules_file:
        config = yaml.safe_load(rules_file)

    if not isinstance(config, dict):
        raise ValueError(f"Rules config must contain a YAML mapping: {config_path}")
    return config
