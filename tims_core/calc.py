"""Calculations: SD, expanded uncertainty, atom % abundance and its uncertainty.

Rules (URD Section 6.4):
  SD = R x RSD(ppm) x 1e-6
  u  = SD                     (not divided by sqrt(n); user decision Q-01)
  U  = k x u, k = 3 by default
  A_i = 100 x R_i / sum(R_j)  (sum includes the reference isotope, R = 1)
Atom % uncertainty uses first-order (GUM) propagation. Ratios are treated as
uncorrelated unless a correlation coefficient ``rho`` is given.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .elements import Element, ratio_label

DEFAULT_K = 3.0


@dataclass
class RatioInput:
    numerator: int
    denominator: int
    value: float
    rsd_ppm: float
    value_raw: str = ""
    rsd_raw: str = ""

    @property
    def label(self) -> str:
        return ratio_label(self.numerator, self.denominator)


@dataclass
class RatioResult:
    label: str
    numerator: int
    denominator: int
    value: float
    rsd_ppm: float
    sd: float
    U: float
    rel_U_ppm: float
    assumed: bool = False  # True when the ratio was not measured but set to natural


@dataclass
class AbundanceResult:
    isotope: int
    atom_pct: float
    U: float
    assumed: bool = False


@dataclass
class CalcResult:
    element: str
    reference_isotope: int
    k: float
    rho: float
    ratios: list[RatioResult] = field(default_factory=list)  # measured ratios only
    assumed_ratios: list[RatioResult] = field(default_factory=list)
    abundances: list[AbundanceResult] = field(default_factory=list)
    assumed_natural: list[int] = field(default_factory=list)
    excluded: list[int] = field(default_factory=list)
    sum_pct: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def ratio(self, label: str) -> RatioResult | None:
        return next((r for r in self.ratios if r.label == label), None)

    def abundance(self, isotope: int) -> AbundanceResult | None:
        return next((a for a in self.abundances if a.isotope == isotope), None)


class CalculationError(ValueError):
    pass


def sd_from_rsd(value: float, rsd_ppm: float) -> float:
    return value * rsd_ppm * 1e-6


def compute(element: Element, inputs: list[RatioInput], k: float = DEFAULT_K, rho: float = 0.0) -> CalcResult:
    """Compute ratio uncertainties and atom % for one sample.

    All inputs must share one denominator, which must be an isotope of the element.
    Atom % does not depend on which isotope is the denominator.
    """
    if not inputs:
        raise CalculationError("no ratios given")
    dens = {r.denominator for r in inputs}
    if len(dens) != 1:
        raise CalculationError("all ratios must have the same denominator isotope")
    den = dens.pop()
    if den not in element.isotopes:
        raise CalculationError(f"denominator {den} is not an isotope of {element.symbol}")
    if not -1.0 <= rho <= 1.0:
        raise CalculationError("correlation coefficient must be between -1 and 1")

    result = CalcResult(element=element.symbol, reference_isotope=den, k=k, rho=rho)

    values: dict[int, float] = {den: 1.0}
    sds: dict[int, float] = {}
    for r in inputs:
        if r.numerator not in element.isotopes:
            raise CalculationError(f"isotope {r.numerator} is not an isotope of {element.symbol}")
        if r.numerator == den or r.numerator in values:
            raise CalculationError(f"ratio {r.label} given more than once or equals the denominator")
        if not (r.value > 0 and math.isfinite(r.value)) or not (r.rsd_ppm >= 0 and math.isfinite(r.rsd_ppm)):
            raise CalculationError(f"ratio {r.label} must have a positive value and non-negative RSD")
        sd = sd_from_rsd(r.value, r.rsd_ppm)
        values[r.numerator] = r.value
        sds[r.numerator] = sd
        result.ratios.append(
            RatioResult(r.label, r.numerator, den, r.value, r.rsd_ppm, sd, k * sd, k * r.rsd_ppm)
        )

    for a in element.mass_numbers():
        if a in values:
            continue
        if element.isotopes[a].abundance <= 0:
            continue
        if element.unmeasured_isotopes == "assume_natural":
            nat = element.natural_ratio(a, den)
            values[a] = nat
            result.assumed_natural.append(a)
            result.assumed_ratios.append(RatioResult(ratio_label(a, den), a, den, nat, 0.0, 0.0, 0.0, 0.0, True))
        else:
            result.excluded.append(a)

    total = sum(values.values())
    measured = list(sds)
    for i in sorted(values):
        r_i = values[i]
        # Jacobian row for A_i with respect to each measured ratio j
        jac = {j: 100.0 * ((total - r_i) if j == i else -r_i) / total**2 for j in measured}
        var = 0.0
        for j in measured:
            for m in measured:
                cov = sds[j] ** 2 if j == m else rho * sds[j] * sds[m]
                var += jac[j] * jac[m] * cov
        result.abundances.append(
            AbundanceResult(i, 100.0 * r_i / total, k * math.sqrt(max(var, 0.0)), i in result.assumed_natural)
        )
    result.sum_pct = sum(a.atom_pct for a in result.abundances)
    return result


def convert_reference(ratios: list[RatioResult], new_den: int, k: float = DEFAULT_K) -> list[RatioResult]:
    """Re-express ratios against another isotope (FR-CAL-05): R_i/s = R_i/r / R_s/r.

    Relative uncertainties are combined in quadrature (ratios treated as uncorrelated).
    """
    by_num = {r.numerator: r for r in ratios}
    if new_den not in by_num:
        raise CalculationError(f"isotope {new_den} is not among the ratios")
    s = by_num[new_den]
    old_den = s.denominator
    out: list[RatioResult] = []
    rel_s = s.rsd_ppm
    # the old denominator becomes the ratio old_den/new_den = 1 / R_s
    entries = [(old_den, 1.0, 0.0, False)] + [
        (r.numerator, r.value, r.rsd_ppm, r.assumed) for r in ratios if r.numerator != new_den
    ]
    for num, val, rel, assumed in sorted(entries):
        value = val / s.value
        rel_new = math.hypot(rel, rel_s)
        sd = value * rel_new * 1e-6
        out.append(RatioResult(ratio_label(num, new_den), num, new_den, value, rel_new, sd, k * sd, k * rel_new, assumed))
    return out


# ----- rounding (FR-CAL-14) -------------------------------------------------

def _decimals_for(u: Decimal, sig: int) -> int:
    exponent = u.adjusted()  # power of ten of the leading digit
    return sig - 1 - exponent


def round_with_uncertainty(value: float, U: float, sig: int = 2, default_decimals: int = 4) -> tuple[str, str]:
    """Round U to ``sig`` significant figures and the value to the same decimal place.

    Returns strings, e.g. (0.185665, 1.862e-4) -> ('0.18567', '0.00019').
    When U is zero (e.g. an assumed natural value) the value gets ``default_decimals``
    places and U is returned as '–'.
    """
    if not U or U <= 0 or not math.isfinite(U):
        q = Decimal(1).scaleb(-default_decimals)
        return str(Decimal(repr(value)).quantize(q, rounding=ROUND_HALF_UP)), "–"
    u = Decimal(repr(U))
    decimals = _decimals_for(u, sig)
    u_r = u.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    # rounding can add a digit (0.0995 -> 0.100); recompute the place from the rounded value
    decimals2 = _decimals_for(u_r, sig)
    if decimals2 != decimals:
        decimals = decimals2
        u_r = u.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    q = Decimal(1).scaleb(-decimals)
    v_r = Decimal(repr(value)).quantize(q, rounding=ROUND_HALF_UP)
    return _plain(v_r), _plain(u_r)


def _plain(d: Decimal) -> str:
    """Format without scientific notation; keep trailing zeros that carry meaning."""
    s = format(d, "f")
    return s
