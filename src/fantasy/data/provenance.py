"""Raw-data caching with provenance sidecars.

Every raw file `data/raw/<source>/<name>` has a sibling `<name>.provenance.json` with
url, fetched_at (UTC), method, sha256, rows, notes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = REPO_ROOT / "data" / "raw"
MANUAL_DIR = REPO_ROOT / "data" / "manual"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def provenance_path(path: Path) -> Path:
    return path.with_name(path.name + ".provenance.json")


def write_provenance(
    path: Path,
    *,
    url: str,
    method: str,
    rows: int | None,
    notes: str = "",
    extra: dict[str, Any] | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    prov = {
        "path": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
        "url": url,
        "fetched_at": fetched_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": method,
        "sha256": sha256_file(path),
        "rows": rows,
        "notes": notes,
    }
    if extra:
        prov.update(extra)
    provenance_path(path).write_text(json.dumps(prov, indent=2) + "\n")
    return prov


def read_provenance(path: Path) -> dict[str, Any] | None:
    p = provenance_path(path)
    return json.loads(p.read_text()) if p.exists() else None
