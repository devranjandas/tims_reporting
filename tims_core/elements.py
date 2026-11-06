"""Element definitions: loading and validating the per-element YAML files.

Each element (Yb, Lu, ...) is described by one YAML file. See
``elements/_template.yaml`` and Section 10 of docs/URD.md for the format.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ABUNDANCE_SUM_TOLERANCE = 0.01  # atom %
UNMEASURED_POLICIES = ("assume_natural", "exclude")

DEFAULT_TOLERANCES: dict[str, Any] = {
    "natural_deviation_pct": {"warning": 5.0, "error": 20.0},
    "baseline_z": {"warning": 3.0, "error": 5.0},
    "enriched_deviation_pct": {"warning": 2.0, "error": 10.0},
    "rsd_ppm": {"min": 1.0, "max": 5000.0},
}


class ElementDefinitionError(ValueError):
    """Raised when an element definition is invalid. ``problems`` lists every issue found."""

    def __init__(self, problems: list[str], source: str = ""):
        self.problems = problems
        self.source = source
        prefix = f"{source}: " if source else ""
        super().__init__(prefix + "; ".join(problems))


@dataclass(frozen=True)
class Isotope:
    mass_number: int
    mass: float  # exact atomic mass (u)
    abundance: float  # natural abundance, atom %
    u_abundance: float = 0.0  # 1-sigma uncertainty of the natural abundance


@dataclass
class Element:
    symbol: str
    name: str
    atomic_number: int
    definition_version: str
    reference_isotope: int
    measured_ratios: list[int]  # numerator mass numbers normally measured
    unmeasured_isotopes: str
    isotopes: dict[int, Isotope]
    abundance_source: str = ""
    interferences: dict[int, list[str]] = field(default_factory=dict)
    tolerances: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_TOLERANCES))
    per_ratio_tolerances: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # ----- helpers -------------------------------------------------------
    def mass_numbers(self) -> list[int]:
        return sorted(self.isotopes)

    def expected_labels(self) -> list[str]:
        return [ratio_label(n, self.reference_isotope) for n in self.measured_ratios]

    def natural_ratio(self, numerator: int, denominator: int | None = None) -> float:
        den = self.reference_isotope if denominator is None else denominator
        return self.isotopes[numerator].abundance / self.isotopes[den].abundance

    def natural_abundance(self, mass_number: int) -> float:
        return self.isotopes[mass_number].abundance

    def tolerance(self, kind: str, label: str | None = None) -> dict[str, float]:
        """Tolerance block ``kind`` (e.g. 'natural_deviation_pct'), with any per-ratio override."""
        tol = dict(self.tolerances.get(kind, DEFAULT_TOLERANCES[kind]))
        if label and label in self.per_ratio_tolerances:
            tol.update(self.per_ratio_tolerances[label].get(kind, {}))
        return tol

    def isotope_label(self, mass_number: int) -> str:
        return f"{mass_number}{self.symbol}"


def ratio_label(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}"


def parse_ratio_label(label: str) -> tuple[int, int]:
    num, den = label.split("/")
    return int(num), int(den)


# ----- loading -------------------------------------------------------------

def _require(d: dict, key: str, problems: list[str], where: str = "") -> Any:
    if key not in d or d[key] in (None, ""):
        problems.append(f"missing required field '{where}{key}'")
        return None
    return d[key]


def element_from_dict(data: Any, source: str = "") -> Element:
    """Build and validate an :class:`Element` from parsed YAML. Raises ElementDefinitionError."""
    problems: list[str] = []
    if not isinstance(data, dict):
        raise ElementDefinitionError(["file does not contain a YAML mapping"], source)

    el = data.get("element")
    if not isinstance(el, dict):
        raise ElementDefinitionError(["missing 'element' section"], source)
    symbol = _require(el, "symbol", problems, "element.")
    name = _require(el, "name", problems, "element.")
    z = _require(el, "atomic_number", problems, "element.")
    version = str(el.get("definition_version", "") or "")
    if not version:
        problems.append("missing required field 'element.definition_version'")

    iso_list = data.get("isotopes")
    isotopes: dict[int, Isotope] = {}
    if not isinstance(iso_list, list) or not iso_list:
        problems.append("'isotopes' must be a non-empty list")
        iso_list = []
    for i, item in enumerate(iso_list):
        if not isinstance(item, dict):
            problems.append(f"isotope #{i + 1} is not a mapping")
            continue
        try:
            a = int(item["A"])
            mass = float(item["mass"])
            ab = float(item["abundance"])
            u_ab = float(item.get("u_abundance", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            problems.append(f"isotope #{i + 1} needs numeric A, mass and abundance")
            continue
        if a in isotopes:
            problems.append(f"isotope {a} is listed more than once")
        if mass <= 0:
            problems.append(f"isotope {a}: mass must be positive")
        if ab < 0:
            problems.append(f"isotope {a}: abundance cannot be negative")
        if u_ab < 0:
            problems.append(f"isotope {a}: u_abundance cannot be negative")
        isotopes[a] = Isotope(a, mass, ab, u_ab)

    natural = [a for a, iso in isotopes.items() if iso.abundance > 0]
    if isotopes and len(natural) < 2:
        problems.append(
            "element has only one naturally occurring isotope (mono-isotopic); "
            "no isotope ratio can be measured"
        )
    total = sum(iso.abundance for iso in isotopes.values())
    if isotopes and abs(total - 100.0) > ABUNDANCE_SUM_TOLERANCE:
        problems.append(f"natural abundances sum to {total:.4f} %, not 100 % (± {ABUNDANCE_SUM_TOLERANCE})")

    ref = data.get("reference_isotope")
    try:
        ref = int(ref)
    except (TypeError, ValueError):
        problems.append("'reference_isotope' must be a mass number")
        ref = None
    if ref is not None and isotopes and ref not in isotopes:
        problems.append(f"reference isotope {ref} is not in the isotope list")
    if ref is not None and ref in isotopes and isotopes[ref].abundance <= 0:
        problems.append(f"reference isotope {ref} has zero natural abundance")

    measured = data.get("measured_ratios") or []
    measured_ints: list[int] = []
    if not isinstance(measured, list) or not measured:
        problems.append("'measured_ratios' must be a non-empty list of numerator mass numbers")
        measured = []
    for m in measured:
        try:
            m = int(m)
        except (TypeError, ValueError):
            problems.append(f"measured ratio numerator '{m}' is not a mass number")
            continue
        if isotopes and m not in isotopes:
            problems.append(f"measured ratio numerator {m} is not in the isotope list")
        if m == ref:
            problems.append(f"measured ratio numerator {m} equals the reference isotope")
        if m in measured_ints:
            problems.append(f"measured ratio numerator {m} is listed twice")
        measured_ints.append(m)

    policy = data.get("unmeasured_isotopes", "assume_natural")
    if policy not in UNMEASURED_POLICIES:
        problems.append(f"'unmeasured_isotopes' must be one of {', '.join(UNMEASURED_POLICIES)}")

    interferences: dict[int, list[str]] = {}
    for item in data.get("interferences") or []:
        try:
            interferences[int(item["mass"])] = [str(s) for s in item.get("species", [])]
        except (KeyError, TypeError, ValueError):
            problems.append(f"invalid interference entry: {item!r}")

    tolerances = copy.deepcopy(DEFAULT_TOLERANCES)
    per_ratio: dict[str, dict[str, Any]] = {}
    qc = data.get("qc_tolerances") or {}
    if not isinstance(qc, dict):
        problems.append("'qc_tolerances' must be a mapping")
        qc = {}
    for kind, block in qc.items():
        if kind == "per_ratio":
            if isinstance(block, dict):
                per_ratio = {str(k): v for k, v in block.items() if isinstance(v, dict)}
            continue
        if kind not in DEFAULT_TOLERANCES:
            problems.append(f"unknown tolerance '{kind}'")
            continue
        if not isinstance(block, dict):
            problems.append(f"tolerance '{kind}' must be a mapping")
            continue
        try:
            tolerances[kind].update({k: float(v) for k, v in block.items()})
        except (TypeError, ValueError):
            problems.append(f"tolerance '{kind}' values must be numbers")
    for kind in ("natural_deviation_pct", "baseline_z", "enriched_deviation_pct"):
        t = tolerances[kind]
        if not 0 < t["warning"] < t["error"]:
            problems.append(f"tolerance '{kind}': need 0 < warning < error")
    if not 0 < tolerances["rsd_ppm"]["min"] < tolerances["rsd_ppm"]["max"]:
        problems.append("tolerance 'rsd_ppm': need 0 < min < max")

    if problems:
        raise ElementDefinitionError(problems, source)

    return Element(
        symbol=str(symbol),
        name=str(name),
        atomic_number=int(z),
        definition_version=version,
        reference_isotope=ref,
        measured_ratios=measured_ints,
        unmeasured_isotopes=policy,
        isotopes=dict(sorted(isotopes.items())),
        abundance_source=str(data.get("abundance_source", "") or ""),
        interferences=interferences,
        tolerances=tolerances,
        per_ratio_tolerances=per_ratio,
        raw=data,
    )


def load_element_text(text: str, source: str = "") -> Element:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ElementDefinitionError([f"not valid YAML: {exc}"], source) from exc
    return element_from_dict(data, source)


def load_element(path: str | Path) -> Element:
    path = Path(path)
    return load_element_text(path.read_text(encoding="utf-8"), path.name)


class ElementLibrary:
    """All element definitions in a folder. Files starting with '_' are ignored."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.elements: dict[str, Element] = {}
        self.errors: dict[str, str] = {}
        self.files: dict[str, Path] = {}
        self.reload()

    def reload(self) -> None:
        self.elements.clear()
        self.errors.clear()
        self.files.clear()
        if not self.folder.is_dir():
            return
        for path in sorted(self.folder.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            try:
                el = load_element(path)
            except ElementDefinitionError as exc:
                self.errors[path.name] = str(exc)
                continue
            if el.symbol in self.elements:
                self.errors[path.name] = f"duplicate definition for {el.symbol}"
                continue
            self.elements[el.symbol] = el
            self.files[el.symbol] = path

    def get(self, symbol: str) -> Element:
        return self.elements[symbol]

    def __contains__(self, symbol: str) -> bool:
        return symbol in self.elements

    def symbols(self) -> list[str]:
        return sorted(self.elements, key=lambda s: self.elements[s].atomic_number)
