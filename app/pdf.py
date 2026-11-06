"""PDF rendering of reports from report JSON (URD 9.3). The PDF depends only on the JSON and the template."""

from __future__ import annotations

import base64
from collections import OrderedDict

from flask import render_template
from markupsafe import Markup

TEMPLATE_VERSION = "1.0"
FORMAT_B_COLUMNS = 4


def _iso(mass, symbol) -> Markup:
    return Markup(f"<sup>{int(mass)}</sup>{symbol}")


def build_notes(data: dict) -> list[Markup]:
    samples = data["samples"]
    notes: list[Markup] = []
    ks = {s.get("k") for s in samples}
    ns = {s.get("n_measurements") for s in samples}
    k_txt = "3" if ks == {3.0} else ", ".join(f"{k:g}" for k in sorted(k for k in ks if k is not None))
    n_txt = f"n = {ns.pop()}" if len(ns) == 1 else "n as stated for each sample"
    notes.append(Markup(f"U = {k_txt} × SD, where SD is the standard deviation of the {n_txt} measurements."))
    by_el: OrderedDict[str, dict] = OrderedDict()
    for s in samples:
        e = by_el.setdefault(s["element"], {"refs": set(), "assumed": set(), "source": s.get("abundance_source", "")})
        if s.get("reference_isotope"):
            e["refs"].add(s["reference_isotope"])
        e["assumed"].update(s.get("assumed_natural") or [])
    for sym, e in by_el.items():
        if e["refs"]:
            refs = ", ".join(str(_iso(r, sym)) for r in sorted(e["refs"]))
            notes.append(Markup(f"{sym} ratios are measured against {refs}."))
        if e["assumed"]:
            iso = ", ".join(str(_iso(a, sym)) for a in sorted(e["assumed"]))
            src = f" ({e['source']})" if e["source"] else ""
            notes.append(Markup(f"* {iso} was not measured; its natural isotope ratio{src} was assumed."))
    notes.append(Markup("No mass-bias correction applied."))
    if data["content"] == "atom_percent":
        rhos = {s.get("rho") or 0.0 for s in samples}
        corr = "ratios treated as uncorrelated" if rhos == {0.0} else \
            "ratio correlation " + ", ".join(f"ρ = {r:g}" for r in sorted(rhos))
        notes.append(Markup(f"Atom % uncertainties are propagated from the ratio uncertainties ({corr})."))
    unapproved = [s for s in samples if s.get("status") != "Approved"]
    if unapproved:
        names = ", ".join(f"{s['sample_id']} ({s['status']})" for s in unapproved)
        notes.append(Markup(f"<strong>DRAFT:</strong> these samples are not approved: {names}."))
    return notes


def format_b_tables(data: dict) -> list[dict]:
    """Samples side by side, up to FORMAT_B_COLUMNS per table."""
    samples = data["samples"]
    tables = []
    for start in range(0, len(samples), FORMAT_B_COLUMNS):
        chunk = samples[start:start + FORMAT_B_COLUMNS]
        if data["content"] == "ratios":
            keys = list(OrderedDict.fromkeys(r["label"] for s in chunk for r in s["ratios"]))
            rows = []
            for k in keys:
                cells = [next((r for r in s["ratios"] if r["label"] == k), None) for s in chunk]
                rows.append({"key": k, "cells": cells, "assumed": False})
        else:
            keys = list(OrderedDict.fromkeys(a["isotope"] for s in chunk for a in s["atom_percent"]))
            keys.sort()
            rows = []
            for k in keys:
                cells = [next((a for a in s["atom_percent"] if a["isotope"] == k), None) for s in chunk]
                rows.append({"key": k, "cells": cells, "assumed": any(c and c.get("assumed") for c in cells)})
        tables.append({"samples": chunk, "rows": rows})
    return tables


def render_report_html(data: dict, logo: tuple[bytes, str] | None = None, preview: bool = False) -> str:
    logo_uri = None
    if logo:
        logo_uri = f"data:{logo[1]};base64,{base64.b64encode(logo[0]).decode()}"
    return render_template("report_pdf/report.html", d=data, notes=build_notes(data), logo_uri=logo_uri,
                           preview=preview, tables_b=format_b_tables(data) if data["layout"] == "format_b" else [])


def render_pdf(data: dict, logo: tuple[bytes, str] | None = None, preview: bool = False) -> bytes:
    from weasyprint import HTML  # imported here so the rest of the app works without it

    return HTML(string=render_report_html(data, logo, preview)).write_pdf()
