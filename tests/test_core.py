"""Tests for the calculation and checking engine (URD Sections 5 and 11)."""

from pathlib import Path

import pytest

from tims_core import calc, parsing, qc, report_json
from tims_core.elements import ElementDefinitionError, ElementLibrary, load_element_text

ROOT = Path(__file__).resolve().parents[1]
LIB = ElementLibrary(ROOT / "elements")
YB = LIB.get("Yb")
LU = LIB.get("Lu")

SAMPLE_TEXT = """Yb Nat

170/173

0.185665

334.298435

171/173

0.866726

150.192199

172/173

1.334461

244.218881

174/173

1.947261

186.367777

176/173

0.781392

255.236049
"""

YB_ROWS = [
    ("170/173", "0.185665", "334.298435"),
    ("171/173", "0.866726", "150.192199"),
    ("172/173", "1.334461", "244.218881"),
    ("174/173", "1.947261", "186.367777"),
    ("176/173", "0.781392", "255.236049"),
]


def rows(data=YB_ROWS, **changes):
    out = []
    for label, v, r in data:
        if label in changes:
            v, r = changes[label]
        out.append(qc.EntryRow(label, v, r))
    return out


def codes(outcome, severity=None):
    return [f.code for f in outcome.findings if severity is None or f.severity == severity]


def find(outcome, code, target=None):
    return [f for f in outcome.findings if f.code == code and (target is None or f.target == target)]


# ---------------------------------------------------------------- elements

def test_library_loads_yb_and_lu():
    assert LIB.symbols() == ["Yb", "Lu"]
    assert not LIB.errors
    assert YB.reference_isotope == 173
    assert YB.expected_labels() == ["170/173", "171/173", "172/173", "174/173", "176/173"]


def test_invalid_definition_sum():  # AT-09
    text = (ROOT / "elements" / "lu.yaml").read_text().replace("abundance: 2.599", "abundance: 3.599")
    with pytest.raises(ElementDefinitionError, match="sum to"):
        load_element_text(text)


def test_mono_isotopic_rejected():  # AT-09
    text = """
element: {symbol: Tb, name: Terbium, atomic_number: 65, definition_version: "1"}
reference_isotope: 159
measured_ratios: [159]
isotopes:
  - {A: 159, mass: 158.925354, abundance: 100}
"""
    with pytest.raises(ElementDefinitionError, match="mono-isotopic"):
        load_element_text(text)


NEODYMIUM = """
element: {symbol: Nd, name: Neodymium, atomic_number: 60, definition_version: "test"}
reference_isotope: 144
measured_ratios: [142, 143, 145, 146, 148, 150]
isotopes:
  - {A: 142, mass: 141.907729, abundance: 27.152}
  - {A: 143, mass: 142.909820, abundance: 12.174}
  - {A: 144, mass: 143.910093, abundance: 23.798}
  - {A: 145, mass: 144.912579, abundance: 8.293}
  - {A: 146, mass: 145.913123, abundance: 17.189}
  - {A: 148, mass: 147.916899, abundance: 5.756}
  - {A: 150, mass: 149.920902, abundance: 5.638}
interferences:
  - {mass: 142, species: ["142Ce"]}
"""


def test_new_element_from_yaml_only(tmp_path):  # AT-08
    (tmp_path / "nd.yaml").write_text(NEODYMIUM)
    nd = ElementLibrary(tmp_path).get("Nd")
    entries = [qc.EntryRow(f"{a}/144", repr(nd.natural_ratio(a)), "100") for a in nd.measured_ratios]
    out = qc.check_sample(nd, entries, "Natural")
    assert not out.has_errors
    assert abs(out.calc.abundance(144).atom_pct - 23.798) < 1e-9


# ---------------------------------------------------------------- calculations

def test_worked_example_ratios():  # AT-01, URD 5.1
    out = qc.check_sample(YB, rows(), "Natural")
    r = out.calc.ratio("170/173")
    assert r.sd == pytest.approx(6.20676e-5, rel=1e-5)
    assert r.U == pytest.approx(1.86203e-4, rel=1e-5)
    assert calc.round_with_uncertainty(r.value, r.U) == ("0.18567", "0.00019")
    assert calc.round_with_uncertainty(out.calc.ratio("174/173").value, out.calc.ratio("174/173").U) == ("1.9473", "0.0011")
    assert calc.round_with_uncertainty(out.calc.ratio("176/173").value, out.calc.ratio("176/173").U) == ("0.78139", "0.00060")


def test_worked_example_atom_percent():  # AT-01, URD 5.2
    out = qc.check_sample(YB, rows(), "Natural")
    c = out.calc
    expected = {168: 0.1247, 170: 3.0322, 171: 14.1549, 172: 21.7937, 173: 16.3315, 174: 31.8017, 176: 12.7613}
    for iso, pct in expected.items():
        assert c.abundance(iso).atom_pct == pytest.approx(pct, abs=6e-5)
    expected_u = {170: 0.0031, 171: 0.0066, 172: 0.0133, 173: 0.0044, 174: 0.0137, 176: 0.0091}
    for iso, u in expected_u.items():
        assert c.abundance(iso).U == pytest.approx(u, abs=6e-5)
    assert c.assumed_natural == [168]
    # the 168/173 ratio is fixed, but its atom % still moves with the sum of the other ratios
    assert c.abundance(168).assumed and 0 < c.abundance(168).U < 1e-4
    assert c.sum_pct == pytest.approx(100.0, abs=1e-12)


def test_worked_example_has_no_errors_and_deviations_shown():  # AT-01, URD 5.3
    out = qc.check_sample(YB, rows(), "Natural")
    assert not out.has_errors
    assert codes(out, qc.WARNING) == []  # all deviations are inside 5 %
    assert find(out, "QC-13")  # baseline not active yet


def test_correlation_changes_only_abundance_uncertainty():
    a = calc.compute(YB, qc.validate_entries(YB, rows())[0], rho=0.0)
    b = calc.compute(YB, qc.validate_entries(YB, rows())[0], rho=0.5)
    assert a.ratio("170/173").U == b.ratio("170/173").U
    assert b.abundance(170).U == pytest.approx(0.0024, abs=6e-5)
    assert b.abundance(173).U == pytest.approx(0.0069, abs=6e-5)


def test_lutetium():  # AT-11
    out = qc.check_sample(LU, [qc.EntryRow("176/175", "0.02668", "300")], "Natural")
    assert not out.has_errors and not codes(out, qc.WARNING)
    assert out.calc.abundance(175).atom_pct == pytest.approx(97.4014, abs=1e-3)
    assert out.calc.abundance(176).atom_pct == pytest.approx(2.5986, abs=1e-3)
    assert out.calc.abundance(176).U > 0


def test_convert_reference():
    res = calc.compute(YB, qc.validate_entries(YB, rows())[0])
    conv = {r.label: r for r in calc.convert_reference(res.ratios, 172)}
    assert conv["173/172"].value == pytest.approx(1 / 1.334461)
    assert conv["174/172"].value == pytest.approx(1.947261 / 1.334461)
    assert conv["174/172"].rsd_ppm == pytest.approx((186.367777**2 + 244.218881**2) ** 0.5)


def test_other_denominator_gives_same_atom_percent():
    import dataclasses
    yb = dataclasses.replace(YB, unmeasured_isotopes="exclude")  # "assume natural" depends on the denominator
    base = calc.compute(yb, qc.validate_entries(yb, rows())[0])
    conv = calc.convert_reference(base.ratios, 172)
    entries = [qc.EntryRow(r.label, repr(r.value), "100") for r in conv]
    out = qc.check_sample(yb, entries, "Natural")
    assert not out.has_errors
    for a in base.abundances:
        assert out.calc.abundance(a.isotope).atom_pct == pytest.approx(a.atom_pct, rel=1e-9)


@pytest.mark.parametrize("value,U,expected", [
    (0.185665, 1.86203e-4, ("0.18567", "0.00019")),
    (21.7937, 0.0133, ("21.794", "0.013")),
    (12.34, 0.0995, ("12.34", "0.10")),
    (1234.5, 123.0, ("1230", "120")),
    (0.1247, 0.0, ("0.1247", "–")),
])
def test_rounding(value, U, expected):
    assert calc.round_with_uncertainty(value, U) == expected


# ---------------------------------------------------------------- import

def test_parse_default_block():  # AT-01 import
    profiles, errors = parsing.load_profiles(ROOT / "import_profiles")
    assert not errors
    res = parsing.parse_auto(SAMPLE_TEXT, profiles)
    assert res.unread == []
    assert len(res.blocks) == 1
    b = res.blocks[0]
    assert (b.element, b.sample) == ("Yb", "Nat")
    assert [(r.label_raw, r.mean_raw, r.rsd_raw) for r in b.rows] == YB_ROWS


def test_parse_csv_and_multiple_blocks():  # AT-13
    profiles, _ = parsing.load_profiles(ROOT / "import_profiles")
    text = "Ratio,Mean,RSD\nYb S1\n170/173,0.185665,334.3\n171/173,0.866726,150.2\nYb S2\n170Yb/173Yb,0.18567,300\n"
    res = parsing.parse_auto(text, profiles)
    assert res.profile.startswith("CSV")
    assert [b.sample for b in res.blocks] == ["S1", "S2"]
    assert res.blocks[1].rows[0].numerator == 170
    assert res.unread == []


def test_parse_reports_unread_lines():
    res = parsing.parse("Yb Nat\n170/173\n0.185665\n334.3\ngarbage line here\n", parsing.ImportProfile(name="d"))
    assert res.unread == [(5, "garbage line here")]


def test_new_import_profile_without_code(tmp_path):  # AT-13
    (tmp_path / "semi.yaml").write_text(
        "name: semicolon\nlayout: table\ndelimiter: ';'\ncolumns: {label: 1, mean: 2, rsd: 3}\n"
        "decimal_separator: ','\nheader_pattern: ''\n"
    )
    profiles, errors = parsing.load_profiles(tmp_path)
    assert not errors
    res = parsing.parse("1;170/173;0,185665;334,3\n2;171/173;0,866726;150,2\n", profiles[0])
    assert [(r.mean_raw, r.rsd_raw) for r in res.blocks[0].rows] == [("0.185665", "334.3"), ("0.866726", "150.2")]


# ---------------------------------------------------------------- entry mistakes

def test_decimal_shift():  # AT-02
    out = qc.check_sample(YB, rows(**{"174/173": ("19.47261", "186.367777")}), "Natural")
    assert find(out, "QC-10", "174/173")[0].severity == qc.ERROR
    s = find(out, "QC-20", "174/173")[0].suggestion
    assert s.kind == "replace_value" and s.new_value == "1.947261"


def test_inverted_ratio():  # AT-03
    out = qc.check_sample(YB, rows(**{"170/173": ("5.386", "334.298435")}), "Natural")
    assert find(out, "QC-10", "170/173")[0].severity == qc.ERROR
    s = find(out, "QC-21", "170/173")[0].suggestion
    assert float(s.new_value) == pytest.approx(0.18567, abs=1e-5)


def test_rows_swapped():  # AT-04
    out = qc.check_sample(YB, rows(**{"171/173": ("1.334461", "150.192199"), "172/173": ("0.866726", "244.218881")}), "Natural")
    s = find(out, "QC-22")[0].suggestion
    assert {s.label, s.other_label} == {"171/173", "172/173"}


def test_rsd_in_percent():  # AT-05
    out = qc.check_sample(YB, rows(**{"170/173": ("0.185665", "0.0334")}), "Natural")
    assert find(out, "QC-02", "170/173:rsd")[0].severity == qc.WARNING
    assert float(find(out, "QC-26", "170/173:rsd")[0].suggestion.new_rsd) == pytest.approx(334)


def test_mean_and_rsd_swapped():  # AT-06
    out = qc.check_sample(YB, rows(**{"170/173": ("334.298435", "0.185665")}), "Natural")
    assert find(out, "QC-10", "170/173")[0].severity == qc.ERROR
    s = find(out, "QC-23", "170/173")[0].suggestion
    assert (s.new_value, s.new_rsd) == ("0.185665", "334.298435")


def test_transposed_digits():
    out = qc.check_sample(YB, rows(**{"170/173": ("0.158665", "334.298435")}), "Natural")
    s = find(out, "QC-24", "170/173")[0].suggestion
    assert s.new_value == "0.185665"


def test_bad_numbers_and_labels():
    out = qc.check_sample(YB, rows(**{"170/173": ("abc", "334"), "171/173": ("-1", "")}), "Natural")
    assert len(find(out, "QC-01")) == 3
    out = qc.check_sample(YB, [qc.EntryRow("176/175", "0.1", "100")], "Natural")
    assert find(out, "QC-03")[0].severity == qc.ERROR  # 175 is not a Yb isotope


def test_missing_and_duplicate_ratio():
    data = YB_ROWS[:-1] + [("171/173", "0.866726", "150.192199")]
    out = qc.check_sample(YB, rows(data), "Natural")
    msgs = " ".join(f.message for f in find(out, "QC-03"))
    assert "more than once" in msgs and "176/173 is missing" in msgs


def test_many_ratios_far_from_natural_suggests_enriched():
    data = [("170/173", "0.05", "300"), ("171/173", "0.3", "150"), ("172/173", "0.4", "200"),
            ("174/173", "0.6", "200"), ("176/173", "9.5", "100")]
    out = qc.check_sample(YB, rows(data), "Natural")
    assert find(out, "QC-25")


# ---------------------------------------------------------------- baseline

def _baseline(n=10):
    import random
    rnd = random.Random(1)
    runs = []
    for _ in range(n):
        runs.append({lbl: float(v) * (1 + rnd.gauss(0, 2e-4)) for lbl, v, _ in YB_ROWS})
    return runs


def test_baseline_activates_at_ten_runs():  # AT-12
    stats9 = qc.baseline_stats(_baseline(9))
    out = qc.check_sample(YB, rows(), "Natural", baseline=qc.Baseline(1, stats9))
    assert find(out, "QC-13")
    stats = qc.baseline_stats(_baseline(10))
    assert all(s.n == 10 for s in stats.values())
    out = qc.check_sample(YB, rows(), "Natural", baseline=qc.Baseline(1, stats))
    assert not find(out, "QC-13")


def test_baseline_catches_small_error_and_mass_bias_outlier():
    stats = qc.baseline_stats(_baseline(10))
    # 2 % low on one ratio: inside the IUPAC tolerance, but far outside the baseline
    out = qc.check_sample(YB, rows(**{"172/173": ("1.30778", "244.218881")}), "Natural", baseline=qc.Baseline(1, stats))
    assert not find(out, "QC-10")
    assert find(out, "QC-11", "172/173")[0].severity == qc.ERROR
    assert find(out, "QC-15", "172/173")[0].severity == qc.WARNING


def test_baseline_outlier_detection():
    runs = _baseline(10)
    runs[3]["176/173"] *= 1.01
    names = [f"S{i}" for i in range(10)]
    msgs = qc.baseline_outliers(runs, names)
    assert any(m.startswith("S3: 176/173") for m in msgs)


# ---------------------------------------------------------------- enriched

ENR = [("170/173", "0.01", "900"), ("171/173", "0.05", "600"), ("172/173", "0.08", "500"),
       ("174/173", "0.15", "400"), ("176/173", "45.0", "100")]


def test_enriched_without_reference():  # AT-07
    out = qc.check_sample(YB, rows(ENR), "Enriched", enriched_isotope=176)
    assert not out.has_errors
    assert find(out, "QC-40") and find(out, "QC-44")
    assert not find(out, "QC-42")


def test_enriched_against_batch_reference():  # AT-07
    expected = qc.ExpectedComposition({lbl: float(v) for lbl, v, _ in ENR}, "sample ENR-1")
    bad = rows(ENR, **{"176/173": ("4.50", "100")})
    out = qc.check_sample(YB, bad, "Enriched", expected=expected, enriched_isotope=176)
    assert find(out, "QC-41", "176/173")[0].severity == qc.ERROR
    assert find(out, "QC-20", "176/173")[0].suggestion.new_value == "45.0"


def test_natural_sample_marked_enriched():  # AT-07
    out = qc.check_sample(YB, rows(), "Enriched", enriched_isotope=176)
    assert find(out, "QC-25")
    assert find(out, "QC-42")


def test_wrong_enriched_isotope():
    out = qc.check_sample(YB, rows(ENR), "Enriched", enriched_isotope=171)
    assert "not enriched in 171Yb" in find(out, "QC-42")[0].message
    double = [("170/173", "0.05", "900"), ("171/173", "0.2", "600"), ("172/173", "0.3", "500"),
              ("174/173", "10", "400"), ("176/173", "8", "100")]
    out = qc.check_sample(YB, rows(double), "Enriched", enriched_isotope=174)
    assert "more enriched" in find(out, "QC-42")[0].message


def test_interference_note_for_176():
    out = qc.check_sample(YB, rows(**{"176/173": ("0.9", "255")}), "Natural")
    assert find(out, "QC-30", "176/173")


# ---------------------------------------------------------------- report JSON

def test_report_json_checksum():  # AT-19
    data = report_json.seal({"schema_version": 1, "report_id": "TIMS/ACD/2026/0001", "revision": 0,
                             "samples": [], "content": "atom_percent", "layout": "format_a"})
    text = report_json.dumps(data)
    assert report_json.loads_verified(text)["report_id"] == "TIMS/ACD/2026/0001"
    with pytest.raises(report_json.ReportJSONError, match="checksum"):
        report_json.loads_verified(text.replace("atom_percent", "ratios"))
    assert report_json.file_stem("TIMS/ACD/2026/0007", 1) == "TIMS_ACD_2026_0007-R1"


def test_snapshot_diff():
    a = {"sample_id": "S", "ratios": [{"label": "170/173", "value": 0.1, "U": 0.001}], "atom_percent": []}
    b = {"sample_id": "S", "ratios": [{"label": "170/173", "value": 0.2, "U": 0.001}], "atom_percent": []}
    assert report_json.diff_snapshots(a, a) == []
    assert "170/173 value" in report_json.diff_snapshots(a, b)[0]
