"""End-to-end tests of the Flask app (URD Section 11)."""

import json
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from app import create_app, db
from app.models import AuditLog, Report, Sample, User, now
from tests.test_core import SAMPLE_TEXT, YB_ROWS

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct-horse-1"


@pytest.fixture()
def app(tmp_path):
    elements = tmp_path / "elements"
    shutil.copytree(ROOT / "elements", elements)
    app = create_app({
        "INSTANCE_DIR": str(tmp_path / "instance"),
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "REPORTS_DIR": str(tmp_path / "reports"),
        "HISTORY_DIR": str(tmp_path / "history"),
        "ELEMENTS_DIR": str(elements),
        "IMPORT_PROFILES_DIR": str(ROOT / "import_profiles"),
        "WTF_CSRF_ENABLED": False,
        "TESTING": True,
    })
    Path(app.config["REPORTS_DIR"]).mkdir(exist_ok=True)
    with app.app_context():
        for username, role, name in (("admin", "admin", "Admin User"), ("ana", "analyst", "Ana Lyst"),
                                     ("rev", "reviewer", "Rev Iewer"), ("vic", "viewer", "Vic Viewer")):
            u = User(username=username, full_name=name, designation=f"{role} designation", role=role,
                     must_change_password=False)
            u.set_password(PASSWORD)
            db.session.add(u)
        db.session.commit()
    yield app


def client_for(app, username):
    c = app.test_client()
    r = c.post("/login", data={"username": username, "password": PASSWORD})
    assert r.status_code == 302, r.data
    return c


def entry_form(rows=YB_ROWS, **meta):
    data = {"element": "Yb", "sample_id": "YB-NAT-01", "sample_type": "Natural", "analysis_date": "2026-10-18",
            "instrument": "TIMS-1", "analyst_name": "Ana Lyst", "n_measurements": "30", "run_id": "", "batch_ref": "",
            "enriched_isotope": "", "reference_sample": "", "remarks": "", "rsd_unit": "ppm", "action": "save"}
    data.update(meta)
    data["label"] = [r[0] for r in rows]
    data["value"] = [r[1] for r in rows]
    data["rsd"] = [r[2] for r in rows]
    return data


def create_sample(c, rows=YB_ROWS, **meta) -> int:
    r = c.post("/samples/new", data=entry_form(rows, **meta))
    assert r.status_code == 302, r.data.decode()[:2000]
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def approve(app, pk, ana, rev):
    assert ana.post(f"/samples/{pk}/submit").status_code == 302
    assert rev.post(f"/samples/{pk}/approve", data={"comment": "ok"}).status_code == 302
    with app.app_context():
        assert db.session.get(Sample, pk).status == "Approved"


def report_form(sample_ids, **extra):
    data = {"sample": [str(i) for i in sample_ids], "division_code": "ACD", "organisation": "Test Organisation",
            "division": "Analytical Chemistry Division", "section": "Mass Spectrometry Section", "address": "Somewhere",
            "form_number": "F-01", "content": "atom_percent", "layout": "format_a",
            "sig0_name": "Ana Lyst", "sig0_designation": "Scientific Officer",
            "sig1_name": "Rev Iewer", "sig1_designation": "Head, MSS", "remarks": "Measured on filament 3.",
            "action": "generate"}
    data.update(extra)
    return data


def pdf_text(path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


# ---------------------------------------------------------------- login and roles

def test_login_lockout(app):  # AT-15c
    c = app.test_client()
    for _ in range(5):
        assert c.post("/login", data={"username": "ana", "password": "wrong"}).status_code == 401
    r = c.post("/login", data={"username": "ana", "password": PASSWORD})
    assert r.status_code == 401 and b"locked" in r.data
    with app.app_context():
        assert db.session.query(AuditLog).filter_by(action="account_locked").count() == 1
        u = db.session.query(User).filter_by(username="ana").one()
        u.locked_until = now() - timedelta(minutes=1)
        db.session.commit()
    assert c.post("/login", data={"username": "ana", "password": PASSWORD}).status_code == 302


def test_must_change_password(app):
    with app.app_context():
        db.session.query(User).filter_by(username="ana").one().must_change_password = True
        db.session.commit()
    c = client_for(app, "ana")
    assert "/password" in c.get("/samples/").headers["Location"]
    r = c.post("/password", data={"current": PASSWORD, "new": "short", "confirm": "short"})
    assert b"at least 10" in r.data
    assert c.post("/password", data={"current": PASSWORD, "new": "a-much-better-1", "confirm": "a-much-better-1"}).status_code == 302
    assert c.get("/samples/").status_code == 200


def test_viewer_sees_reports_only(app):
    c = client_for(app, "vic")
    assert c.get("/samples/").status_code == 403
    assert c.get("/reports/").status_code == 200
    assert c.get("/admin/users").status_code == 403


def test_admin_creates_user(app):
    c = client_for(app, "admin")
    r = c.post("/admin/users/new", data={"username": "newbie", "full_name": "New Bie", "designation": "SO", "role": "analyst"},
               follow_redirects=True)
    m = re.search(rb"Temporary password for newbie: (\S+)", r.data)
    assert m
    c2 = app.test_client()
    assert c2.post("/login", data={"username": "newbie", "password": m.group(1).decode()}).status_code == 302
    assert "/password" in c2.get("/").headers["Location"]


# ---------------------------------------------------------------- entry and checks

def test_paste_then_save(app):  # AT-01 in the app
    c = client_for(app, "ana")
    form = entry_form([], sample_id="")
    form.update(action="parse", paste=SAMPLE_TEXT, profile="auto")
    r = c.post("/samples/new", data=form)
    html = r.data.decode()
    assert 'value="0.866726"' in html and 'value="Nat"' in html and "Read 5 ratios" in html
    pk = create_sample(c)
    page = c.get(f"/samples/{pk}").data.decode()
    assert "0.18567 ± 0.00019" in page
    assert "3.0322 ± 0.0031" in page
    assert "QC-13" in page  # baseline not active yet
    with app.app_context():
        s = db.session.get(Sample, pk)
        assert s.results["assumed_natural"] == [168]
        assert not [f for f in s.findings if f["severity"] in ("ERROR", "WARNING")]


def test_duplicate_sample_id_refused(app):
    c = client_for(app, "ana")
    create_sample(c)
    r = c.post("/samples/new", data=entry_form())
    assert r.status_code == 400 and b"already exists" in r.data


def test_suggestion_accepted_and_logged(app):  # AT-02
    c = client_for(app, "ana")
    rows = [r if r[0] != "174/173" else ("174/173", "19.47261", r[2]) for r in YB_ROWS]
    pk = create_sample(c, rows)
    with app.app_context():
        s = db.session.get(Sample, pk)
        key = next(f["key"] for f in s.findings if f["code"] == "QC-20")
    assert c.post(f"/samples/{pk}/submit", follow_redirects=True).data.count(b"cannot be submitted") == 1
    c.post(f"/samples/{pk}/suggestion", data={"key": key})
    with app.app_context():
        s = db.session.get(Sample, pk)
        assert next(e for e in s.entries if e["label"] == "174/173")["value"] == "1.947261"
        assert not [f for f in s.findings if f["severity"] == "ERROR"]
        assert db.session.query(AuditLog).filter_by(action="suggestion_accepted").count() == 1


def test_warning_needs_justification_and_error_needs_reviewer(app):
    ana, rev = client_for(app, "ana"), client_for(app, "rev")
    rows = [r if r[0] != "176/173" else ("176/173", "0.86", r[2]) for r in YB_ROWS]  # +6.6 % -> WARNING
    pk = create_sample(ana, rows)
    with app.app_context():
        key = next(f["key"] for f in db.session.get(Sample, pk).findings if f["severity"] == "WARNING")
    ana.post(f"/samples/{pk}/submit")
    with app.app_context():
        assert db.session.get(Sample, pk).status == "Draft"
    ana.post(f"/samples/{pk}/justify", data={f"j_{key}": "Confirmed by re-run"})
    ana.post(f"/samples/{pk}/submit")
    with app.app_context():
        assert db.session.get(Sample, pk).status == "Submitted"
    # an ERROR on another sample: only a reviewer can override it, and not on their own data
    rows = [r if r[0] != "176/173" else ("176/173", "1.2", r[2]) for r in YB_ROWS]
    pk2 = create_sample(ana, rows, sample_id="YB-2")
    with app.app_context():
        key2 = next(f["key"] for f in db.session.get(Sample, pk2).findings if f["severity"] == "ERROR")
    assert ana.post(f"/samples/{pk2}/override", data={"key": key2, "comment": "x"}).status_code == 403
    rev.post(f"/samples/{pk2}/override", data={"key": key2, "comment": "Interference confirmed; accepted"})
    with app.app_context():
        f = next(f for f in db.session.get(Sample, pk2).findings if f["key"] == key2)
        assert f["overridden_by"] == "Rev Iewer"


def test_review_workflow_and_versions(app):  # AT-10
    ana, rev = client_for(app, "ana"), client_for(app, "rev")
    pk = create_sample(ana)
    ana.post(f"/samples/{pk}/submit")
    assert ana.post(f"/samples/{pk}/approve").status_code == 403  # analysts cannot approve
    rev.post(f"/samples/{pk}/approve")
    with app.app_context():
        s = db.session.get(Sample, pk)
        assert s.status == "Approved" and len(s.versions) == 1
    assert ana.get(f"/samples/{pk}/edit").status_code == 302  # locked
    ana.post(f"/samples/{pk}/revise", data={"reason": "typo in 170/173"})
    with app.app_context():
        s = db.session.get(Sample, pk)
        assert (s.status, s.version) == ("Draft", 2)
        assert s.versions[0].snapshot["status"] == "Approved"
    assert b"sample_revision_started" in ana.get(f"/samples/{pk}/history").data


def test_reviewer_cannot_approve_own(app):
    rev = client_for(app, "rev")
    pk = create_sample(rev)
    rev.post(f"/samples/{pk}/submit")
    assert rev.post(f"/samples/{pk}/approve").status_code == 403


def test_concurrent_edit_refused(app):  # AT-14
    a, b = client_for(app, "ana"), client_for(app, "rev")
    pk = create_sample(a)
    with app.app_context():
        lock = db.session.get(Sample, pk).lock_version
    form = entry_form(remarks="first")
    form["lock_version"] = str(lock)
    assert a.post(f"/samples/{pk}/edit", data=form).status_code == 302
    form["remarks"] = "second"
    r = b.post(f"/samples/{pk}/edit", data=form)
    assert r.status_code == 409 and b"Someone else saved" in r.data
    with app.app_context():
        assert db.session.get(Sample, pk).remarks == "first"


def test_bulk_import_and_csv(app):
    c = client_for(app, "ana")
    text = SAMPLE_TEXT.replace("Yb Nat", "Yb NAT-A") + "\n" + SAMPLE_TEXT.replace("Yb Nat", "Yb NAT-B")
    r = c.post("/samples/bulk", data={"paste": text, "profile": "auto", "sample_type": "Natural",
                                      "analysis_date": "2026-10-01", "instrument": "TIMS-1", "n_measurements": "30"})
    assert r.data.count(b"created as draft (0 errors, 0 warnings)") == 2
    csv_text = c.get("/samples/export.csv").data.decode()
    assert csv_text.count("NAT-A") == 12 and "atom_percent" in csv_text


# ---------------------------------------------------------------- baseline

def test_baseline_activates_fine_check(app):  # AT-12
    ana, rev = client_for(app, "ana"), client_for(app, "rev")
    ids = []
    for i in range(10):
        f = 1 + (i - 4.5) * 4e-5
        rows = [(lbl, f"{float(v) * f:.6f}", rsd) for lbl, v, rsd in YB_ROWS]
        pk = create_sample(ana, rows, sample_id=f"NAT-{i:02d}")
        approve(app, pk, ana, rev)
        ids.append(pk)
    assert ana.post("/baselines/Yb", data={"member": ids, "action": "save"}).status_code == 403
    r = rev.post("/baselines/Yb", data={"member": [str(i) for i in ids], "action": "save"}, follow_redirects=True)
    assert b"Baseline version 1" in r.data and b"<svg" in r.data
    rows = [r if r[0] != "172/173" else ("172/173", "1.30778", r[2]) for r in YB_ROWS]  # 2 % low
    pk = create_sample(ana, rows, sample_id="NEW-1")
    with app.app_context():
        s = db.session.get(Sample, pk)
        f = [f for f in s.findings if f["code"] == "QC-11"]
        assert f and f[0]["severity"] == "ERROR" and s.reference_info["baseline_version"] == 1


def test_enriched_uses_batch_reference(app):  # AT-07
    ana, rev = client_for(app, "ana"), client_for(app, "rev")
    enr = [("170/173", "0.01", "900"), ("171/173", "0.05", "600"), ("172/173", "0.08", "500"),
           ("174/173", "0.15", "400"), ("176/173", "45.0", "100")]
    pk1 = create_sample(ana, enr, sample_id="ENR-1", sample_type="Enriched", enriched_isotope="176", batch_ref="B-7")
    approve(app, pk1, ana, rev)
    bad = [r if r[0] != "176/173" else ("176/173", "4.50", "100") for r in enr]
    pk2 = create_sample(ana, bad, sample_id="ENR-2", sample_type="Enriched", enriched_isotope="176", batch_ref="b-7")
    with app.app_context():
        s = db.session.get(Sample, pk2)
        assert s.reference_info["batch_sample"] == "ENR-1"
        assert any(f["code"] == "QC-41" and f["severity"] == "ERROR" for f in s.findings)
        assert any(f["code"] == "QC-20" for f in s.findings)


# ---------------------------------------------------------------- reports

def test_report_generate_recreate_and_revise(app):  # AT-15, AT-16, AT-17, AT-18
    ana, rev = client_for(app, "ana"), client_for(app, "rev")
    pk1 = create_sample(ana)
    approve(app, pk1, ana, rev)
    pk2 = create_sample(ana, sample_id="YB-NAT-02")
    approve(app, pk2, ana, rev)
    r = ana.post("/reports/new", data=report_form([pk1, pk2]))
    assert r.status_code == 302
    year = now().year
    with app.app_context():
        rep = db.session.query(Report).one()
        assert rep.report_id == f"TIMS/ACD/{year}/0001" and not rep.draft
        stem = rep.file_stem
    rdir = Path(app.config["REPORTS_DIR"])
    data = json.loads((rdir / f"{stem}.json").read_text())
    assert data["header"]["division"] == "Analytical Chemistry Division"
    assert [s["sample_id"] for s in data["samples"]] == ["YB-NAT-01", "YB-NAT-02"]
    text = pdf_text(rdir / f"{stem}.pdf")
    for expected in (f"TIMS/ACD/{year}/0001", "Analytical Chemistry Division", "Test Organisation", "Prepared by",
                     "Ana Lyst", "Head, MSS", "Remarks:", "Measured on filament 3.", "3.0322", "0.0031",
                     "This report is digitally generated and does not need signatures.", "Page 1 of"):
        assert expected in text, expected
    assert "DRAFT" not in text
    assert "____" not in text  # names and designations only, no signature lines
    original = text

    # AT-17: delete the PDF and recreate it from the JSON
    (rdir / f"{stem}.pdf").unlink()
    page = ana.get(f"/reports/{rep.id}/recreate").data.decode()
    assert "checksum and signature verified" in page and "still match" in page
    r = ana.get(f"/reports/{rep.id}/recreate.pdf")
    assert r.status_code == 200 and r.data.startswith(b"%PDF")
    assert pdf_text(rdir / f"{stem}.pdf") == original

    # AT-18: change a sample after reporting
    ana.post(f"/samples/{pk1}/revise", data={"reason": "re-measured"})
    with app.app_context():
        lock = db.session.get(Sample, pk1).lock_version
    rows = [r if r[0] != "170/173" else ("170/173", "0.185700", r[2]) for r in YB_ROWS]
    form = entry_form(rows)
    form["lock_version"] = str(lock)
    ana.post(f"/samples/{pk1}/edit", data=form)
    page = ana.get(f"/reports/{rep.id}/recreate").data.decode()
    assert "newer version" in page and "still match" not in page
    ana.get(f"/reports/{rep.id}/recreate.pdf")
    assert "3.0322" in original

    # AT-16 + revision: ratios table, same Report ID with -R1
    r = ana.post("/reports/new", data=report_form([pk2], content="ratios", revise_of=str(rep.id),
                                                   revision_reason="ratios requested"))
    assert r.status_code == 302
    with app.app_context():
        r1 = db.session.query(Report).filter_by(revision=1).one()
        assert r1.report_id == rep.report_id and r1.file_stem.endswith("-R1")
    text = pdf_text(rdir / f"{r1.file_stem}.pdf")
    assert f"TIMS/ACD/{year}/0001-R1" in text and "0.18567" in text and "0.00019" in text
    assert f"replaces TIMS/ACD/{year}/0001" in text


def test_draft_report_and_saved_header(app):  # AT-15a, AT-15b
    ana = client_for(app, "ana")
    pk = create_sample(ana)  # never approved
    r = ana.post("/reports/new", data=report_form([pk], save_header_name="ACD header", layout="format_b"))
    assert r.status_code == 302
    with app.app_context():
        rep = db.session.query(Report).one()
        assert rep.draft
    rdir = Path(app.config["REPORTS_DIR"])
    text = pdf_text(rdir / f"{rep.file_stem}.pdf")
    assert "DRAFT" in text and "not approved" in text
    # editing the saved header does not change the generated report
    with app.app_context():
        from app.models import SavedHeader
        h = db.session.query(SavedHeader).one()
        hid = h.id
    ana.post(f"/reports/headers/{hid}", data={"name": "ACD header", "division_code": "ACD", "organisation": "Renamed Org",
                                              "division": "Other Division"})
    ana.get(f"/reports/{rep.id}/recreate.pdf")
    (rdir / f"{rep.file_stem}.pdf").unlink()
    ana.get(f"/reports/{rep.id}/recreate.pdf")
    text = pdf_text(rdir / f"{rep.file_stem}.pdf")
    assert "Test Organisation" in text and "Renamed Org" not in text


def test_report_ids_are_sequential(app):
    ana = client_for(app, "ana")
    pk = create_sample(ana)
    ana.post("/reports/new", data=report_form([pk]))
    ana.post("/reports/new", data=report_form([pk], division_code="XYZ"))
    with app.app_context():
        ids = sorted(r.report_id for r in db.session.query(Report).all())
    year = now().year
    assert ids == [f"TIMS/ACD/{year}/0001", f"TIMS/XYZ/{year}/0002"]


def test_report_form_validation(app):
    ana = client_for(app, "ana")
    r = ana.post("/reports/new", data=report_form([], division_code="1", sig1_name=""))
    html = r.data.decode()
    assert r.status_code == 400
    assert "Division code" in html and "Select at least one sample" in html and "Checked / Approved by" in html


def test_preview_pdf(app):
    ana = client_for(app, "ana")
    pk = create_sample(ana)
    r = ana.post("/reports/new", data=report_form([pk], action="preview"))
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    with app.app_context():
        assert db.session.query(Report).count() == 0


def test_tampered_json_refused_and_upload_recreate(app):  # AT-19
    ana = client_for(app, "ana")
    pk = create_sample(ana)
    ana.post("/reports/new", data=report_form([pk]))
    with app.app_context():
        rep = db.session.query(Report).one()
    text = (Path(app.config["REPORTS_DIR"]) / f"{rep.file_stem}.json").read_text()
    from io import BytesIO
    r = ana.post("/reports/upload", data={"report_json": (BytesIO(text.encode()), "r.json")}, follow_redirects=True)
    assert b"checksum and signature verified" in r.data
    r = ana.post("/reports/upload", data={"json_text": text, "render": "1"})
    assert r.data.startswith(b"%PDF")
    bad = text.replace("Test Organisation", "Fake Organisation")
    r = ana.post("/reports/upload", data={"report_json": (BytesIO(bad.encode()), "r.json")}, follow_redirects=True)
    assert b"checksum does not match" in r.data
    # a forger who recomputes the plain checksum still fails the signature check
    from tims_core import report_json
    forged = report_json.dumps(report_json.seal(json.loads(bad)))
    r = ana.post("/reports/upload", data={"report_json": (BytesIO(forged.encode()), "r.json")}, follow_redirects=True)
    assert b"not generated by this installation" in r.data


# ---------------------------------------------------------------- admin files

NEODYMIUM = (ROOT / "tests" / "test_core.py").read_text().split('NEODYMIUM = """')[1].split('"""')[0]


def test_admin_adds_element_and_rejects_bad_file(app):  # AT-08, AT-09
    c = client_for(app, "admin")
    r = c.post("/admin/files/elements/edit", data={"name": "", "text": NEODYMIUM}, follow_redirects=True)
    assert b"nd.yaml saved" in r.data
    ana = client_for(app, "ana")
    assert b"Neodymium (Nd)" in ana.get("/samples/new?element=Nd").data
    bad = NEODYMIUM.replace("abundance: 5.638", "abundance: 6.638").replace('"test"', '"test2"')
    r = c.post("/admin/files/elements/edit", data={"name": "nd.yaml", "text": bad})
    assert r.status_code == 400 and b"sum to" in r.data
    same_version = NEODYMIUM.replace("142Ce", "142Ce, 142X")
    r = c.post("/admin/files/elements/edit", data={"name": "nd.yaml", "text": same_version})
    assert r.status_code == 400 and b"definition_version" in r.data


def test_admin_settings_change_k(app):
    c = client_for(app, "admin")
    c.post("/admin/settings", data={"coverage_k": "2", "ratio_correlation": "0", "sig0": "Prepared by",
                                    "sig1": "Approved by", "footer_statement": "Digitally generated."})
    ana = client_for(app, "ana")
    pk = create_sample(ana)
    with app.app_context():
        s = db.session.get(Sample, pk)
        assert s.results["k"] == 2.0
    assert b"Approved by" in ana.get("/reports/new").data


def test_backup_command(app, tmp_path):
    import tarfile
    ana = client_for(app, "ana")
    create_sample(ana)
    result = app.test_cli_runner().invoke(args=["backup", "--dest", str(tmp_path / "bk")])
    assert result.exit_code == 0, result.output
    archive = next((tmp_path / "bk").glob("tims-*.tar.gz"))
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert any(n.endswith("/tims.db") for n in names) and any("/elements/yb.yaml" in n for n in names)


def test_missing_pango_gives_clear_message(app, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "weasyprint", None)  # makes "import weasyprint" fail
    ana = client_for(app, "ana")
    pk = create_sample(ana)
    r = ana.post("/reports/new", data=report_form([pk]))
    assert r.status_code == 503 and b"Pango" in r.data
    with app.app_context():
        assert db.session.query(Report).count() == 0
    monkeypatch.delitem(sys.modules, "weasyprint")
    ana.post("/reports/new", data=report_form([pk]))
    with app.app_context():  # the failed attempt did not use up report number 0001
        assert db.session.query(Report).one().report_id.endswith("/0001")
    assert not any(Path(app.config["REPORTS_DIR"]).glob("*_0002*"))
