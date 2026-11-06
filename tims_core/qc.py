"""Consistency checks and diagnosis of data-entry mistakes (URD Section 6.5).

The main entry point is :func:`check_sample`. It validates the entered rows,
runs the calculation, then compares the result with the right reference for the
sample type:

* Natural / Standard: IUPAC natural ratios (coarse) and the lab baseline (fine).
* Enriched: an earlier approved measurement of the same batch, if there is one.
* Unknown: deviations from natural are shown for information only.

Flagged values are then tested against common typing mistakes, and a likely
correct value is suggested. Nothing is ever changed automatically.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation

from .calc import CalcResult, CalculationError, RatioInput, compute
from .elements import Element, ratio_label

INFO, WARNING, ERROR = "INFO", "WARNING", "ERROR"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}

SAMPLE_TYPES = ("Natural", "Standard", "Enriched", "Unknown")
NATURAL_TYPES = ("Natural", "Standard")

_LABEL_RE = re.compile(
    r"^\s*(?:R\s*)?(?P<num>\d{1,3})\s*(?:[A-Z][a-z]?)?\s*/\s*(?P<den>\d{1,3})\s*(?:[A-Z][a-z]?)?\s*$"
)


@dataclass
class Suggestion:
    kind: str  # replace_value | replace_rsd | swap_rows | swap_value_rsd
    label: str
    new_value: str | None = None  # text to put in the field
    other_label: str | None = None
    new_rsd: str | None = None


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    target: str | None = None
    suggestion: Suggestion | None = None

    @property
    def key(self) -> str:
        return f"{self.code}:{self.target or ''}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


@dataclass
class EntryRow:
    label: str
    value: str
    rsd: str


@dataclass
class BaselineStat:
    mean: float
    sd: float
    n: int


@dataclass
class Baseline:
    version: int
    stats: dict[str, BaselineStat]
    min_runs: int = 10

    def is_active_for(self, label: str) -> bool:
        s = self.stats.get(label)
        return bool(s and s.n >= self.min_runs and s.sd > 0)


@dataclass
class ExpectedComposition:
    """Reference composition for an enriched material (ratios on the same denominator)."""

    ratios: dict[str, float]
    source: str = ""


@dataclass
class CheckOutcome:
    inputs: list[RatioInput]
    calc: CalcResult | None
    findings: list[Finding] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == ERROR for f in self.findings)

    def by_severity(self, severity: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]


# ---------------------------------------------------------------------------
# helpers

def deviation_pct(value: float, reference: float) -> float:
    return (value / reference - 1.0) * 100.0


def _to_float(text: str) -> float | None:
    try:
        v = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _fmt(v: float, sig: int = 6) -> str:
    return f"{v:.{sig}g}"


def _grade(abs_value: float, tol: dict[str, float]) -> str | None:
    if abs_value > tol["error"]:
        return ERROR
    if abs_value > tol["warning"]:
        return WARNING
    return None


# ---------------------------------------------------------------------------
# basic validation (QC-01, QC-03)

def validate_entries(element: Element, rows: list[EntryRow], rsd_unit: str = "ppm") -> tuple[list[RatioInput], list[Finding]]:
    findings: list[Finding] = []
    inputs: list[RatioInput] = []
    seen: set[str] = set()
    dens: set[int] = set()

    for idx, row in enumerate(rows, start=1):
        if not (row.label.strip() or row.value.strip() or row.rsd.strip()):
            continue  # empty row in the form
        m = _LABEL_RE.match(row.label or "")
        if not m:
            findings.append(Finding("QC-03", ERROR, f"Row {idx}: '{row.label}' is not a ratio label like 170/173.", row.label or f"row{idx}"))
            continue
        num, den = int(m["num"]), int(m["den"])
        label = ratio_label(num, den)
        bad = [a for a in (num, den) if a not in element.isotopes]
        if bad:
            findings.append(Finding("QC-03", ERROR, f"{label}: {', '.join(map(str, bad))} is not an isotope of {element.name}.", label))
            continue
        if num == den:
            findings.append(Finding("QC-03", ERROR, f"{label}: numerator and denominator are the same isotope.", label))
            continue
        if label in seen:
            findings.append(Finding("QC-03", ERROR, f"{label} is entered more than once.", label))
            continue
        seen.add(label)
        dens.add(den)

        ok = True
        value = _to_float(row.value)
        if not row.value.strip():
            findings.append(Finding("QC-01", ERROR, f"{label}: mean ratio is missing.", label)); ok = False
        elif value is None:
            findings.append(Finding("QC-01", ERROR, f"{label}: mean ratio '{row.value}' is not a number.", label)); ok = False
        elif value <= 0:
            findings.append(Finding("QC-01", ERROR, f"{label}: mean ratio must be greater than zero.", label)); ok = False
        rsd = _to_float(row.rsd)
        if not row.rsd.strip():
            findings.append(Finding("QC-01", ERROR, f"{label}: RSD is missing.", label + ":rsd")); ok = False
        elif rsd is None:
            findings.append(Finding("QC-01", ERROR, f"{label}: RSD '{row.rsd}' is not a number.", label + ":rsd")); ok = False
        elif rsd <= 0:
            findings.append(Finding("QC-01", ERROR, f"{label}: RSD must be greater than zero.", label + ":rsd")); ok = False
        if not ok:
            continue
        if rsd_unit == "percent":
            rsd_ppm = rsd * 1e4
        elif rsd_unit == "absolute":
            rsd_ppm = rsd / value * 1e6
        else:
            rsd_ppm = rsd
        inputs.append(RatioInput(num, den, value, rsd_ppm, row.value.strip(), row.rsd.strip()))

    if len(dens) > 1:
        findings.append(Finding("QC-03", ERROR, "All ratios must use the same denominator isotope "
                                f"(found {', '.join(map(str, sorted(dens)))}).", None))
        return inputs, findings

    den = dens.pop() if dens else element.reference_isotope
    expected_nums = (set(element.measured_ratios) | {element.reference_isotope}) - {den}
    entered_nums = {int(lbl.split("/")[0]) for lbl in seen}
    for n in sorted(expected_nums - entered_nums):
        findings.append(Finding("QC-03", ERROR, f"Expected ratio {ratio_label(n, den)} is missing.", ratio_label(n, den)))
    for n in sorted(entered_nums - expected_nums):
        findings.append(Finding("QC-03", WARNING, f"Ratio {ratio_label(n, den)} is not normally measured for {element.name}.", ratio_label(n, den)))
    return inputs, findings


# ---------------------------------------------------------------------------
# main entry point

def check_sample(
    element: Element,
    rows: list[EntryRow],
    sample_type: str,
    rsd_unit: str = "ppm",
    baseline: Baseline | None = None,
    expected: ExpectedComposition | None = None,
    enriched_isotope: int | None = None,
    k: float = 3.0,
    rho: float = 0.0,
) -> CheckOutcome:
    if sample_type not in SAMPLE_TYPES:
        raise ValueError(f"sample type must be one of {SAMPLE_TYPES}")
    inputs, findings = validate_entries(element, rows, rsd_unit)
    outcome = CheckOutcome(inputs=inputs, calc=None, findings=findings)
    if any(f.severity == ERROR and f.code == "QC-03" for f in findings) or not inputs:
        return outcome
    if any(f.severity == ERROR for f in findings):
        # some numbers are unusable: still run what we can on the valid rows
        pass
    try:
        calc = compute(element, inputs, k=k, rho=rho)
    except CalculationError as exc:
        findings.append(Finding("QC-03", ERROR, str(exc)))
        return outcome
    outcome.calc = calc
    by_label = {r.label: r for r in inputs}

    _check_rsd(element, inputs, findings)

    reference: dict[str, float] = {}
    ref_tol_kind = None
    flagged: dict[str, str] = {}  # label -> severity of the reference check

    natural = {r.label: element.natural_ratio(r.numerator, r.denominator) for r in inputs}

    if sample_type in NATURAL_TYPES:
        for r in inputs:
            tol = element.tolerance("natural_deviation_pct", r.label)
            d = deviation_pct(r.value, natural[r.label])
            sev = _grade(abs(d), tol)
            if sev:
                flagged[r.label] = sev
                findings.append(Finding("QC-10", sev,
                    f"{r.label} = {_fmt(r.value)} is {d:+.2f} % from the natural value {_fmt(natural[r.label])} "
                    f"(limits ±{tol['warning']:g} % / ±{tol['error']:g} %).", r.label))
        _check_baseline(element, inputs, baseline, findings, flagged)
        _check_abundance(element, calc, findings, flagged)
        reference, ref_tol_kind = natural, "natural_deviation_pct"
    else:
        devs = ", ".join(f"{r.label} {deviation_pct(r.value, natural[r.label]):+.1f} %" for r in inputs)
        findings.append(Finding("QC-40", INFO, f"Deviation from natural (for information only): {devs}."))
        if sample_type == "Enriched":
            if expected and expected.ratios:
                for r in inputs:
                    if r.label not in expected.ratios:
                        continue
                    tol = element.tolerance("enriched_deviation_pct", r.label)
                    d = deviation_pct(r.value, expected.ratios[r.label])
                    sev = _grade(abs(d), tol)
                    if sev:
                        flagged[r.label] = sev
                        findings.append(Finding("QC-41", sev,
                            f"{r.label} = {_fmt(r.value)} is {d:+.2f} % from the batch reference {_fmt(expected.ratios[r.label])} "
                            f"({expected.source}; limits ±{tol['warning']:g} % / ±{tol['error']:g} %).", r.label))
                reference = {lbl: v for lbl, v in expected.ratios.items() if lbl in by_label}
                ref_tol_kind = "enriched_deviation_pct"
            else:
                findings.append(Finding("QC-44", INFO,
                    "No earlier approved measurement of this batch was found, so only basic and plausibility checks were run."))
            _check_enrichment(element, inputs, calc, enriched_isotope, natural, findings)

    diagnosed = _diagnose(element, inputs, reference, ref_tol_kind, flagged, findings) if ref_tol_kind else set()

    if sample_type in NATURAL_TYPES:
        hard = [lbl for lbl, sev in flagged.items() if sev == ERROR and lbl not in diagnosed]
        if len(inputs) >= 2 and len(hard) >= max(2, math.ceil(len(inputs) / 2)):
            findings.append(Finding("QC-25", INFO,
                "Most ratios are far from natural. If this is an enriched material, change the sample type to Enriched."))
        _check_interference(element, inputs, flagged, diagnosed, findings)

    if abs(calc.sum_pct - 100.0) > 1e-9:
        findings.append(Finding("QC-32", ERROR, f"Internal check failed: atom % sum to {calc.sum_pct:.12f}."))

    outcome.findings = sorted(findings, key=lambda f: SEVERITY_ORDER[f.severity])
    return outcome


# ---------------------------------------------------------------------------
# individual checks

def _check_rsd(element: Element, inputs: list[RatioInput], findings: list[Finding]) -> None:
    for r in inputs:
        tol = element.tolerance("rsd_ppm", r.label)
        lo, hi = tol["min"], tol["max"]
        if lo <= r.rsd_ppm <= hi:
            continue
        sugg = None
        candidates = [
            (r.rsd_ppm * 1e4, "the RSD may have been entered in % instead of ppm"),
            (r.rsd_ppm / 1e4, "the RSD may have been entered in ppm while '%' was selected"),
            (r.rsd_ppm / r.value * 1e6 if r.value else -1, "an absolute SD may have been entered instead of the RSD"),
        ]
        for cand, why in candidates:
            if lo <= cand <= hi:
                sugg = (cand, why)
                break
        msg = f"{r.label}: RSD {_fmt(r.rsd_ppm)} ppm is outside the usual range {lo:g}–{hi:g} ppm."
        findings.append(Finding("QC-02", WARNING, msg, r.label + ":rsd"))
        if sugg:
            findings.append(Finding("QC-26", INFO,
                f"{r.label}: {sugg[1]}. In ppm it would be {_fmt(sugg[0])}.", r.label + ":rsd",
                Suggestion("replace_rsd", r.label, new_rsd=_fmt(sugg[0]))))


def _check_baseline(element: Element, inputs: list[RatioInput], baseline: Baseline | None,
                    findings: list[Finding], flagged: dict[str, str]) -> None:
    active = [r for r in inputs if baseline and baseline.is_active_for(r.label)]
    if not active:
        n = 0
        if baseline and baseline.stats:
            n = min(s.n for s in baseline.stats.values())
        need = baseline.min_runs if baseline else 10
        findings.append(Finding("QC-13", INFO,
            f"The lab baseline for {element.name} is not active yet ({n} of {need} runs), so the fine check was not run."))
        return
    tol = element.tolerance("baseline_z")
    z_flagged = []
    for r in active:
        s = baseline.stats[r.label]
        z = (r.value - s.mean) / s.sd
        if flagged.get(r.label) == ERROR:
            continue  # already blocked by the natural check
        sev = _grade(abs(z), element.tolerance("baseline_z", r.label))
        if sev:
            z_flagged.append(r.label)
            if SEVERITY_ORDER[sev] < SEVERITY_ORDER.get(flagged.get(r.label, INFO), 2):
                flagged[r.label] = sev
            findings.append(Finding("QC-11", sev,
                f"{r.label} = {_fmt(r.value)} is {z:+.1f} SD from the lab baseline {_fmt(s.mean)} "
                f"(SD {_fmt(s.sd, 3)}, n = {s.n}; limits ±{tol['warning']:g} / ±{tol['error']:g}).", r.label))
    _check_mass_bias(element, active, baseline, z_flagged, findings)


def _check_mass_bias(element: Element, active: list[RatioInput], baseline: Baseline,
                     z_flagged: list[str], findings: list[Finding]) -> None:
    """QC-15: one wrong entry stands out from the common mass-bias trend of the other ratios."""
    if len(active) < 3 or not z_flagged:
        return
    betas: dict[str, tuple[float, float]] = {}
    for r in active:
        s = baseline.stats[r.label]
        lm = math.log(element.isotopes[r.numerator].mass / element.isotopes[r.denominator].mass)
        if r.value <= 0 or s.mean <= 0 or lm == 0:
            continue
        beta = math.log(r.value / s.mean) / lm
        sigma = math.hypot(r.rsd_ppm * 1e-6, s.sd / s.mean) / abs(lm)
        betas[r.label] = (beta, sigma)
    if len(betas) < 3:
        return
    med = statistics.median(b for b, _ in betas.values())
    outliers = [lbl for lbl, (b, sg) in betas.items() if sg > 0 and abs(b - med) / sg > 5]
    for lbl in outliers:
        findings.append(Finding("QC-15", WARNING,
            f"{lbl} does not follow the common mass-bias trend of the other ratios. Check this value.", lbl))
    if not outliers:
        findings.append(Finding("QC-15", INFO,
            f"The deviations from the baseline follow one mass-dependent trend (β ≈ {med:.3f}). "
            "This points to an instrument effect rather than an entry mistake."))


def _check_abundance(element: Element, calc: CalcResult, findings: list[Finding], flagged: dict[str, str]) -> None:
    if flagged:
        return  # the ratio checks already explain the problem
    tol = element.tolerance("natural_deviation_pct")
    for a in calc.abundances:
        if a.assumed:
            continue
        nat = element.natural_abundance(a.isotope)
        d = deviation_pct(a.atom_pct, nat)
        if abs(d) > tol["warning"]:
            findings.append(Finding("QC-16", WARNING,
                f"{element.isotope_label(a.isotope)}: {a.atom_pct:.4f} atom % is {d:+.2f} % from natural ({nat:g} %).",
                str(a.isotope)))


def _check_enrichment(element: Element, inputs: list[RatioInput], calc: CalcResult, enriched_isotope: int | None,
                      natural: dict[str, float], findings: list[Finding]) -> None:
    """QC-42 / QC-25: is the composition consistent with the declared enrichment?"""
    tol = element.tolerance("natural_deviation_pct")
    if all(abs(deviation_pct(r.value, natural[r.label])) <= tol["warning"] for r in inputs):
        findings.append(Finding("QC-25", WARNING,
            "The composition is within the natural tolerance for every ratio. Check that the sample type "
            "'Enriched' is correct."))
    if enriched_isotope is None:
        findings.append(Finding("QC-42", WARNING, "The enriched isotope is not stated for this enriched sample."))
        return
    if enriched_isotope not in element.isotopes:
        findings.append(Finding("QC-42", ERROR, f"{enriched_isotope} is not an isotope of {element.name}."))
        return
    ef = {a.isotope: a.atom_pct / element.natural_abundance(a.isotope)
          for a in calc.abundances if not a.assumed and element.natural_abundance(a.isotope) > 0}
    if enriched_isotope not in ef:
        return
    name = element.isotope_label(enriched_isotope)
    if ef[enriched_isotope] <= 1.0:
        findings.append(Finding("QC-42", WARNING,
            f"The sample is not enriched in {name}: its abundance is {ef[enriched_isotope]:.3f} × natural."))
        return
    top = max(ef, key=ef.get)
    if top != enriched_isotope:
        findings.append(Finding("QC-42", WARNING,
            f"{element.isotope_label(top)} is more enriched ({ef[top]:.2f} × natural) than the declared "
            f"isotope {name} ({ef[enriched_isotope]:.2f} × natural). Check the enriched isotope."))


def _check_interference(element: Element, inputs: list[RatioInput], flagged: dict[str, str],
                        diagnosed: set[str], findings: list[Finding]) -> None:
    if not flagged:
        return
    all_flagged = len(flagged) == len(inputs) and len(inputs) > 1
    if all_flagged:
        return
    for r in inputs:
        if r.label not in flagged or r.label in diagnosed:
            continue
        for mass in (r.numerator, r.denominator):
            species = element.interferences.get(mass)
            if species:
                findings.append(Finding("QC-30", INFO,
                    f"{r.label}: mass {mass} can carry isobaric interference from {', '.join(species)}. "
                    "Check the interference monitors.", r.label))


# ---------------------------------------------------------------------------
# diagnosis of entry mistakes (QC-20 .. QC-24)

def _diagnose(element: Element, inputs: list[RatioInput], reference: dict[str, float], tol_kind: str,
              flagged: dict[str, str], findings: list[Finding]) -> set[str]:
    by_label = {r.label: r for r in inputs}
    targets = [lbl for lbl in flagged if lbl in reference and lbl in by_label]

    def within(label: str, value: float) -> bool:
        if value is None or value <= 0 or not math.isfinite(value):
            return False
        tol = element.tolerance(tol_kind, label)
        return abs(deviation_pct(value, reference[label])) <= tol["warning"]

    done: set[str] = set()
    for lbl in targets:
        r = by_label[lbl]
        # QC-23: mean and RSD swapped
        rsd_num = _to_float(r.rsd_raw)
        if rsd_num is not None and within(lbl, rsd_num):
            findings.append(Finding("QC-23", INFO,
                f"{lbl}: the mean and RSD look swapped. Did you mean mean = {r.rsd_raw}, RSD = {r.value_raw}?", lbl,
                Suggestion("swap_value_rsd", lbl, new_value=r.rsd_raw, new_rsd=r.value_raw)))
            done.add(lbl); continue
        # QC-21: inverted ratio
        if within(lbl, 1.0 / r.value):
            inv = _fmt(1.0 / r.value)
            num, den = lbl.split("/")
            findings.append(Finding("QC-21", INFO,
                f"{lbl}: the value looks like the inverted ratio {den}/{num}. Did you mean {inv}?", lbl,
                Suggestion("replace_value", lbl, new_value=inv)))
            done.add(lbl); continue
        # QC-20: decimal point shifted
        shifted = _decimal_shift(r.value_raw, lambda v: within(lbl, v))
        if shifted:
            findings.append(Finding("QC-20", INFO,
                f"{lbl}: the decimal point looks shifted. Did you mean {shifted}?", lbl,
                Suggestion("replace_value", lbl, new_value=shifted)))
            done.add(lbl); continue

    # QC-22: two values entered in each other's rows
    rest = [lbl for lbl in targets if lbl not in done]
    for i, a in enumerate(rest):
        for b in rest[i + 1:]:
            if a in done or b in done:
                continue
            if within(a, by_label[b].value) and within(b, by_label[a].value):
                findings.append(Finding("QC-22", INFO,
                    f"The values of {a} and {b} look swapped between rows. Swap them?", a,
                    Suggestion("swap_rows", a, other_label=b)))
                done.update({a, b})

    # QC-24: transposed or mistyped digit
    for lbl in targets:
        if lbl in done:
            continue
        r = by_label[lbl]
        fix, kind = _digit_fix(r.value_raw, reference[lbl], lambda v: within(lbl, v))
        if fix:
            what = "two digits look swapped" if kind == "swap" else "one digit looks mistyped"
            findings.append(Finding("QC-24", INFO,
                f"{lbl}: {what}. Did you mean {fix}?", lbl, Suggestion("replace_value", lbl, new_value=fix)))
            done.add(lbl)
    return done


def _decimal_shift(raw: str, ok) -> str | None:
    try:
        d = Decimal(raw)
    except (InvalidOperation, TypeError):
        return None
    for k in (-1, 1, -2, 2, -3, 3):
        cand = d.scaleb(k)
        if ok(float(cand)):
            return format(cand, "f")
    return None


def _digit_fix(raw: str, reference: float, ok) -> tuple[str | None, str]:
    raw = raw.strip()
    digit_pos = [i for i, ch in enumerate(raw) if ch.isdigit()]
    swaps = []
    for a, b in zip(digit_pos, digit_pos[1:]):
        if b == a + 1 and raw[a] != raw[b]:
            cand = raw[:a] + raw[b] + raw[a] + raw[b + 1:]
            v = _to_float(cand)
            if ok(v):
                swaps.append((abs(v - reference), cand))
    if swaps:
        return min(swaps)[1], "swap"
    significant = raw.lstrip("+-0.").replace(".", "")
    if len(significant) < 4:
        return None, ""  # too few digits to tell a typing slip from a real value
    singles = []
    for i in digit_pos:
        for ch in "0123456789":
            if ch == raw[i]:
                continue
            cand = raw[:i] + ch + raw[i + 1:]
            v = _to_float(cand)
            if ok(v):
                singles.append((abs(v - reference), cand))
    if singles:
        return min(singles)[1], "digit"
    return None, ""


# ---------------------------------------------------------------------------
# lab baseline statistics (FR-QC-12)

def baseline_stats(runs: list[dict[str, float]]) -> dict[str, BaselineStat]:
    """Mean, SD and n per ratio label from a list of runs ({label: value})."""
    labels = sorted({lbl for run in runs for lbl in run})
    out: dict[str, BaselineStat] = {}
    for lbl in labels:
        vals = [run[lbl] for run in runs if lbl in run]
        sd = statistics.stdev(vals) if len(vals) >= 2 else 0.0
        out[lbl] = BaselineStat(statistics.fmean(vals), sd, len(vals))
    return out


def baseline_outliers(runs: list[dict[str, float]], names: list[str], z_limit: float = 3.0) -> list[str]:
    """Leave-one-out check: runs whose ratios are > z_limit SD from the mean of the other runs."""
    msgs = []
    for i, run in enumerate(runs):
        others = runs[:i] + runs[i + 1:]
        if len(others) < 3:
            continue
        stats = baseline_stats(others)
        for lbl, v in run.items():
            s = stats.get(lbl)
            if s and s.sd > 0:
                z = (v - s.mean) / s.sd
                if abs(z) > z_limit:
                    msgs.append(f"{names[i]}: {lbl} is {z:+.1f} SD from the other baseline runs.")
    return msgs
