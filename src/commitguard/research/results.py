"""Immutable benchmark results.

A result is written once, to a new file named after the benchmark, the time and
the CommitGuard version; an existing file is never overwritten. Newer runs do
not replace older ones - historical results stay as they were measured, so an
improvement between versions remains visible and verifiable.

Layout::

    benchmarks/results/raw/<benchmark>/<UTC timestamp>_<version>.json
    benchmarks/results/processed/index.json     (regenerated from raw/)
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from commitguard.research.environment import BenchmarkManifest

RESULT_SCHEMA = 1
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ResultExistsError(FileExistsError):
    pass


def result_document(
    benchmark: str, manifest: BenchmarkManifest, result: BaseModel | dict[str, Any]
) -> dict[str, Any]:
    payload = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    return {
        "schema_version": RESULT_SCHEMA,
        "benchmark": benchmark,
        "manifest": manifest.model_dump(mode="json"),
        "result": payload,
    }


def write_result(root: Path, document: dict[str, Any]) -> Path:
    """Write ``document`` under ``root``/raw and refresh the index. Never overwrites."""
    benchmark = _SAFE.sub("-", str(document["benchmark"]))
    manifest = document["manifest"]
    stamp = datetime.fromisoformat(str(manifest["timestamp"])).astimezone(UTC)
    version = _SAFE.sub("-", str(manifest["commitguard_version"]))
    target = root / "raw" / benchmark / f"{stamp.strftime('%Y%m%dT%H%M%S%fZ')}_{version}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    except FileExistsError:
        raise ResultExistsError(f"{target} already exists; results are never overwritten") from None
    rebuild_index(root)
    return target


def load_results(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    results = []
    for path in sorted((root / "raw").glob("*/*.json")):
        try:
            results.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except ValueError:
            continue
    return results


def latest(root: Path, benchmark: str) -> tuple[Path, dict[str, Any]] | None:
    matching = [item for item in load_results(root) if item[1].get("benchmark") == benchmark]
    return matching[-1] if matching else None


def rebuild_index(root: Path) -> Path:
    entries = []
    for path, document in load_results(root):
        manifest = document.get("manifest", {})
        entries.append(
            {
                "benchmark": document.get("benchmark"),
                "file": path.relative_to(root).as_posix(),
                "timestamp": manifest.get("timestamp"),
                "commitguard_version": manifest.get("commitguard_version"),
                "operating_system": manifest.get("operating_system"),
                "dataset_version": manifest.get("dataset_version"),
                "rules_version": manifest.get("rules_version"),
            }
        )
    index = root / "processed" / "index.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps({"results": entries}, indent=2) + "\n", encoding="utf-8")
    return index
