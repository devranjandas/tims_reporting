"""Import of instrument output through configurable import profiles (FR-IN-02, FR-IN-10).

Two layouts are supported:

* ``line_block``: a header line (element + sample) followed by repeating groups of
  lines, e.g. label / mean / RSD (the default TIMS summary format).
* ``table``: one ratio per line, with columns separated by a delimiter.

Values are kept as the text that was read; numeric checks happen in
:func:`tims_core.qc.validate_entries` so the user sees exactly what was imported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_LABEL_PATTERN = (
    r"^(?:R\s*)?(?P<num>\d{1,3})\s*(?:[A-Z][a-z]?)?\s*/\s*(?P<den>\d{1,3})\s*(?:[A-Z][a-z]?)?$"
)
DEFAULT_HEADER_PATTERN = r"^(?P<element>[A-Z][a-z]?)(?:\s+(?P<sample>.+?))?\s*$"


class ImportProfileError(ValueError):
    pass


@dataclass
class ImportProfile:
    name: str
    layout: str = "line_block"
    header_pattern: str = DEFAULT_HEADER_PATTERN
    label_pattern: str = DEFAULT_LABEL_PATTERN
    group: list[str] = field(default_factory=lambda: ["label", "mean", "rsd"])
    rsd_unit: str = "ppm"  # ppm | percent | absolute
    decimal_separator: str = "."
    delimiter: str | None = None  # table layout; None = any whitespace
    columns: dict[str, int] = field(default_factory=lambda: {"label": 0, "mean": 1, "rsd": 2})
    skip_pattern: str | None = None  # lines to ignore silently
    source: str = ""

    def __post_init__(self) -> None:
        if self.layout not in ("line_block", "table"):
            raise ImportProfileError(f"{self.name}: layout must be 'line_block' or 'table'")
        if self.rsd_unit not in ("ppm", "percent", "absolute"):
            raise ImportProfileError(f"{self.name}: rsd_unit must be ppm, percent or absolute")
        if self.decimal_separator not in (".", ","):
            raise ImportProfileError(f"{self.name}: decimal_separator must be '.' or ','")
        if self.layout == "line_block" and sorted(self.group) != ["label", "mean", "rsd"]:
            raise ImportProfileError(f"{self.name}: group must contain label, mean and rsd once each")
        if self.layout == "table" and not {"label", "mean", "rsd"} <= set(self.columns):
            raise ImportProfileError(f"{self.name}: columns must give label, mean and rsd")
        for pat_name in ("header_pattern", "label_pattern", "skip_pattern"):
            pat = getattr(self, pat_name)
            if pat:
                try:
                    re.compile(pat)
                except re.error as exc:
                    raise ImportProfileError(f"{self.name}: {pat_name} is not a valid pattern: {exc}") from exc
        if "num" not in re.compile(self.label_pattern).groupindex or "den" not in re.compile(self.label_pattern).groupindex:
            raise ImportProfileError(f"{self.name}: label_pattern needs named groups 'num' and 'den'")


def profile_from_dict(data: Any, source: str = "") -> ImportProfile:
    if not isinstance(data, dict) or not data.get("name"):
        raise ImportProfileError(f"{source}: profile needs a 'name'")
    allowed = set(ImportProfile.__dataclass_fields__) - {"source"}
    unknown = set(data) - allowed
    if unknown:
        raise ImportProfileError(f"{source}: unknown field(s) {', '.join(sorted(unknown))}")
    try:
        return ImportProfile(**data, source=source)
    except TypeError as exc:
        raise ImportProfileError(f"{source}: {exc}") from exc


def load_profile_text(text: str, source: str = "") -> ImportProfile:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ImportProfileError(f"{source}: not valid YAML: {exc}") from exc
    return profile_from_dict(data, source)


def load_profiles(folder: str | Path) -> tuple[list[ImportProfile], dict[str, str]]:
    """Load every *.yaml profile in a folder. Returns (profiles, errors by file name)."""
    profiles: list[ImportProfile] = []
    errors: dict[str, str] = {}
    folder = Path(folder)
    if folder.is_dir():
        for path in sorted(folder.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            try:
                profiles.append(load_profile_text(path.read_text(encoding="utf-8"), path.name))
            except ImportProfileError as exc:
                errors[path.name] = str(exc)
    if not profiles:
        profiles.append(ImportProfile(name="TIMS summary block (built-in)"))
    return profiles, errors


@dataclass
class ParsedRow:
    label_raw: str
    numerator: int
    denominator: int
    mean_raw: str
    rsd_raw: str
    line_no: int


@dataclass
class ParsedBlock:
    element: str | None = None
    sample: str | None = None
    rows: list[ParsedRow] = field(default_factory=list)
    header_line: int | None = None


@dataclass
class ParseResult:
    profile: str
    rsd_unit: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    unread: list[tuple[int, str]] = field(default_factory=list)  # (line number, text)

    @property
    def row_count(self) -> int:
        return sum(len(b.rows) for b in self.blocks)


def _norm_number(text: str, decimal_separator: str) -> str:
    text = text.strip()
    if decimal_separator == ",":
        text = text.replace(".", "").replace(",", ".") if text.count(",") == 1 else text
    return text


def parse(text: str, profile: ImportProfile) -> ParseResult:
    lines = [(i + 1, ln.strip()) for i, ln in enumerate(text.splitlines())]
    lines = [(n, ln) for n, ln in lines if ln]
    skip = re.compile(profile.skip_pattern) if profile.skip_pattern else None
    if skip:
        lines = [(n, ln) for n, ln in lines if not skip.search(ln)]
    if profile.layout == "table":
        return _parse_table(lines, profile)
    return _parse_line_block(lines, profile)


def _parse_line_block(lines: list[tuple[int, str]], profile: ImportProfile) -> ParseResult:
    res = ParseResult(profile=profile.name, rsd_unit=profile.rsd_unit)
    label_re = re.compile(profile.label_pattern)
    header_re = re.compile(profile.header_pattern)
    size = len(profile.group)
    i = 0
    block: ParsedBlock | None = None
    while i < len(lines):
        n, ln = lines[i]
        # try to read a full group starting here
        group = lines[i : i + size]
        if len(group) == size:
            by_role = {role: group[j] for j, role in enumerate(profile.group)}
            m = label_re.match(by_role["label"][1])
            if m and _looks_numeric(by_role["mean"][1], profile) and _looks_numeric(by_role["rsd"][1], profile):
                if block is None:
                    block = ParsedBlock()
                    res.blocks.append(block)
                block.rows.append(
                    ParsedRow(
                        label_raw=by_role["label"][1],
                        numerator=int(m["num"]),
                        denominator=int(m["den"]),
                        mean_raw=_norm_number(by_role["mean"][1], profile.decimal_separator),
                        rsd_raw=_norm_number(by_role["rsd"][1], profile.decimal_separator),
                        line_no=by_role["label"][0],
                    )
                )
                i += size
                continue
        h = header_re.match(ln)
        if h and not label_re.match(ln) and not _looks_numeric(ln, profile):
            block = ParsedBlock(element=h.groupdict().get("element"), sample=h.groupdict().get("sample"), header_line=n)
            res.blocks.append(block)
            i += 1
            continue
        res.unread.append((n, ln))
        i += 1
    res.blocks = [b for b in res.blocks if b.rows or b.element]
    return res


def _parse_table(lines: list[tuple[int, str]], profile: ImportProfile) -> ParseResult:
    res = ParseResult(profile=profile.name, rsd_unit=profile.rsd_unit)
    label_re = re.compile(profile.label_pattern)
    header_re = re.compile(profile.header_pattern) if profile.header_pattern else None
    block: ParsedBlock | None = None
    cols = profile.columns
    for n, ln in lines:
        parts = [p.strip() for p in (ln.split(profile.delimiter) if profile.delimiter else ln.split())]
        try:
            label, mean, rsd = parts[cols["label"]], parts[cols["mean"]], parts[cols["rsd"]]
        except IndexError:
            label = mean = rsd = None
        m = label_re.match(label) if label else None
        if m and _looks_numeric(mean, profile) and _looks_numeric(rsd, profile):
            if block is None:
                block = ParsedBlock()
                res.blocks.append(block)
            block.rows.append(
                ParsedRow(label, int(m["num"]), int(m["den"]),
                          _norm_number(mean, profile.decimal_separator),
                          _norm_number(rsd, profile.decimal_separator), n)
            )
            continue
        h = header_re.match(ln) if header_re else None
        if h and len(parts) <= 3 and not _looks_numeric(parts[0], profile):
            block = ParsedBlock(element=h.groupdict().get("element"), sample=h.groupdict().get("sample"), header_line=n)
            res.blocks.append(block)
            continue
        res.unread.append((n, ln))
    res.blocks = [b for b in res.blocks if b.rows or b.element]
    return res


_NUM_RE = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")


def _looks_numeric(text: str | None, profile: ImportProfile) -> bool:
    if text is None:
        return False
    return bool(_NUM_RE.match(text.strip()))


def parse_auto(text: str, profiles: list[ImportProfile]) -> ParseResult:
    """Try each profile and keep the one that reads the most rows with the fewest unread lines."""
    best: ParseResult | None = None
    for p in profiles:
        try:
            r = parse(text, p)
        except Exception:  # a broken profile must not stop the others
            continue
        if best is None or (r.row_count, -len(r.unread)) > (best.row_count, -len(best.unread)):
            best = r
    if best is None:
        best = parse(text, ImportProfile(name="TIMS summary block (built-in)"))
    return best
