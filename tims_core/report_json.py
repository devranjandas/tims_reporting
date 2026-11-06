"""Report JSON: building, checksum and comparison (URD Sections 9.5 and 9.6).

A report JSON holds everything needed to draw the PDF again: header text,
signatories, remarks and a snapshot of every reported value. The checksum makes
any later hand edit detectable.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SCHEMA_VERSION = 1
CHECKSUM_FIELD = "checksum_sha256"


class ReportJSONError(ValueError):
    pass


def canonical_bytes(data: dict[str, Any]) -> bytes:
    body = {k: v for k, v in data.items() if k != CHECKSUM_FIELD}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_checksum(data: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(data)).hexdigest()


def seal(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``data`` with its checksum added."""
    out = dict(data)
    out[CHECKSUM_FIELD] = compute_checksum(out)
    return out


def verify(data: dict[str, Any]) -> bool:
    return isinstance(data, dict) and data.get(CHECKSUM_FIELD) == compute_checksum(data)


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False)


def loads_verified(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportJSONError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ReportJSONError("not a TIMS report file (unknown schema version)")
    if not verify(data):
        raise ReportJSONError("the checksum does not match: the file has been changed since it was generated")
    for key in ("report_id", "revision", "samples", "content", "layout"):
        if key not in data:
            raise ReportJSONError(f"missing field '{key}'")
    return data


def file_stem(report_id: str, revision: int) -> str:
    """File name for a report, e.g. TIMS/ACD/2026/0007 rev 1 -> TIMS_ACD_2026_0007-R1."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", report_id)
    return f"{stem}-R{revision}" if revision else stem


def display_id(report_id: str, revision: int) -> str:
    return f"{report_id}-R{revision}" if revision else report_id


def diff_snapshots(reported: dict[str, Any], current: dict[str, Any], tol: float = 0.0) -> list[str]:
    """List the differences between a reported sample snapshot and the database now."""
    diffs: list[str] = []
    for key in ("sample_id", "element", "sample_type", "reference_isotope", "status"):
        if reported.get(key) != current.get(key):
            diffs.append(f"{key}: reported {reported.get(key)!r}, now {current.get(key)!r}")

    def index(rows, key):
        return {str(r[key]): r for r in rows or []}

    for section, key in (("ratios", "label"), ("atom_percent", "isotope")):
        a, b = index(reported.get(section), key), index(current.get(section), key)
        for k in sorted(set(a) | set(b)):
            if k not in b:
                diffs.append(f"{section} {k}: no longer present")
                continue
            if k not in a:
                diffs.append(f"{section} {k}: added since the report")
                continue
            for field in ("value", "U"):
                va, vb = a[k].get(field), b[k].get(field)
                if va is None or vb is None:
                    if va != vb:
                        diffs.append(f"{section} {k} {field}: reported {va}, now {vb}")
                elif abs(va - vb) > tol * max(abs(va), 1e-300):
                    diffs.append(f"{section} {k} {field}: reported {va:.8g}, now {vb:.8g}")
    return diffs
