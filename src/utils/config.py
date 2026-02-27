"""
Config loading for sol-next.

One YAML file (config/sol.yaml). Loaded once at startup.
Validated once at startup. Then passed as a frozen Config object
to every phase function.

No compilation step. No 3,000-line compiler. Just: load, validate, freeze.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Config:
    """The complete runtime configuration for sol-next.

    Loaded from config/sol.yaml at startup. Immutable after loading.
    Every phase receives this object. No phase reads YAML directly.
    """

    patterns: list[dict[str, Any]]
    behaviors: list[dict[str, Any]]
    atomicizers: dict[str, Any]
    extractors: dict[str, list[str]]
    graph: dict[str, Any]
    thresholds: dict[str, float]
    raw: dict[str, Any]


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate config/sol.yaml. Raises on any validation failure.

    Does not return a partially-valid config. Either the config is correct
    and the pipeline can run, or it raises and the pipeline does not start.
    """
    config_path = Path(path or "config/sol.yaml")
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open() as f:
        raw = yaml.safe_load(f)

    _validate(raw, config_path)

    return Config(
        patterns=raw["patterns"],
        behaviors=raw["behaviors"],
        atomicizers=raw["atomicizers"],
        extractors=raw["extractors"],
        graph=raw["graph"],
        thresholds=raw["thresholds"],
        raw=raw,
    )


def _validate(raw: dict[str, Any], path: Path) -> None:
    """Validate config structure. Raises ValueError with clear message on failure."""
    required = ["patterns", "behaviors", "atomicizers", "extractors", "graph", "thresholds"]
    for key in required:
        if key not in raw:
            raise ValueError(f"config/{path.name} missing required section: '{key}'")

    behavior_ids = {b["id"] for b in raw["behaviors"]}
    pattern_ids = {p["id"] for p in raw["patterns"]}

    for behavior in raw["behaviors"]:
        for required_pattern in behavior.get("requires", []):
            if required_pattern not in pattern_ids:
                raise ValueError(
                    f"Behavior '{behavior['id']}' requires unknown pattern '{required_pattern}'"
                )
        for any_of_pattern in behavior.get("any_of", []):
            if any_of_pattern not in pattern_ids:
                raise ValueError(
                    f"Behavior '{behavior['id']}' any_of references unknown pattern '{any_of_pattern}'"
                )
        for none_of_pattern in behavior.get("none_of", []):
            if none_of_pattern not in pattern_ids:
                raise ValueError(
                    f"Behavior '{behavior['id']}' none_of references unknown pattern '{none_of_pattern}'"
                )

    for behavior_id, extractor_list in raw["extractors"].items():
        if behavior_id not in behavior_ids:
            raise ValueError(
                f"Extractor config references unknown behavior '{behavior_id}'"
            )

    for behavior_id in raw["atomicizers"]:
        if behavior_id not in behavior_ids:
            raise ValueError(
                f"Atomicizer config references unknown behavior '{behavior_id}'"
            )
