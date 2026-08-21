from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:  # pragma: no cover - exercised by installation checks
    raise RuntimeError(
        "jsonschema is required; install the project with `python3 -m pip install -e .`"
    ) from exc


SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Draft202012Validator:
    schema_path = SCHEMA_DIR / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def schema_errors(instance: Any, schema_name: str) -> list[str]:
    errors = sorted(_validator(schema_name).iter_errors(instance), key=lambda error: list(error.path))
    rendered = []
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        rendered.append(f"{location}: {error.message}")
    return rendered
