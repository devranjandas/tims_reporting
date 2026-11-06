# User Requirements Document (URD)
## TIMS Isotopic Composition Reporting Software ("TIMS-Report")

| Item | Value |
|---|---|
| Document ID | TIMS-URD-001 |
| Version | 0.2 (Draft for review) |
| Date | 2026-11-06 |
| Status | Draft: updated with user comments on v0.1 |
| Initial elements | Ytterbium (Yb), Lutetium (Lu); later the stable lanthanides |
| Platform | Web application: Python Flask + SQLite, hosted on the internal (on-premises) cloud |

### Revision history

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-10-19 | First draft |
| 0.2 | 2026-11-06 | Changed to a multi-user Flask/SQLite web app. Removed atomic-weight calculation (FR-CAL-10). Uncertainty fixed as U = 3 × SD. ¹⁶⁸Yb assumed natural. Enriched samples made fully supported. Lab baseline seeded with 10 samples. ISO 17025 items reduced. Lanthanide scope and interference table added. Configurable import formats. Reporting section fully specified (Section 9). Explanations added for uncertainty budget, ratio correlation and mass-bias correction (Appendix A). Open questions re-issued (Section 12). |

---

## 1. Introduction

### 1.1 Purpose
This document sets out what users need from a web application that takes isotope ratio data measured on a Thermal Ionisation Mass Spectrometer (TIMS) and:

1. reports each measured isotope ratio with its **expanded uncertainty U = 3 × SD**,
2. works out the **isotopic composition (atom %)** of the element from those ratios, with uncertainties,
3. **finds inconsistent or wrongly entered data** by comparing it with natural isotopic abundances, the lab's own baseline, or the expected composition of an enriched material,
4. supports **new elements** (stable lanthanides) added through a standard definition file, without code changes,
5. generates **PDF reports** for any samples in the database. Each report has a unique ID, is stored as a JSON file, and can be recreated as a PDF from that JSON.

### 1.2 Scope
In scope:
- Manual entry and import of TIMS summary results (mean ratio + RSD in ppm), in configurable formats.
- Uncertainty calculation and propagation.
- Calculation of atom % abundance.
- Element library (Yb, Lu first, then stable lanthanides).
- Data consistency and plausibility checks for natural and enriched samples.
- Multi-user web access, review and approval workflow, audit trail.
- Report generation, storage (JSON) and regeneration (PDF).

Out of scope for now (see Section 13):
- Direct control of, or live acquisition from, the TIMS instrument.
- Processing raw cycle-by-cycle data.
- Isotope dilution (IDMS) concentration calculations.
- Atomic-weight calculation (removed at user request).
- Radioactive elements and decay correction.

### 1.3 Definitions and abbreviations

| Term | Meaning |
|---|---|
| TIMS | Thermal Ionisation Mass Spectrometry |
| Ratio, Rᵢ/ᵣ | Number of atoms of isotope *i* divided by atoms of reference isotope *r* (e.g. ¹⁷⁰Yb/¹⁷³Yb) |
| Reference (denominator) isotope | Isotope all ratios are expressed against (¹⁷³Yb for Yb, ¹⁷⁵Lu for Lu) |
| n | Number of measurements averaged by the instrument (normally 30) |
| RSD | Relative standard deviation = SD / mean |
| ppm | Parts per million (1 ppm = 10⁻⁶) |
| SD | Standard deviation of the n individual measurements |
| U | Expanded uncertainty. In this software **U = 3 × SD** |
| Atom % | Fraction of all atoms of the element that belong to one isotope, × 100 |
| Natural abundance | Representative isotopic composition of the element in nature (IUPAC / CIAAW) |
| Lab baseline | Mean and SD of the lab's own accepted measurements of a natural sample, per element and instrument |
| Nat | Sample label meaning "natural (not enriched) material" |
| Enriched | Material where one or more isotopes are deliberately increased |
| Mass bias | Instrument effect that makes measured ratios differ from true ones, depending on the mass difference (Appendix A.3) |
| K-factor | Correction factor K = R_true / R_measured, found by measuring a standard |
| Isobaric interference | An isotope of another element with the same mass number (e.g. ¹⁷⁶Lu, ¹⁷⁶Hf on ¹⁷⁶Yb) |
| Report JSON | The stored definition of a generated report, used to recreate its PDF |

### 1.4 References
1. JCGM 100:2008 (GUM). Evaluation of measurement data: guide to the expression of uncertainty in measurement.
2. JCGM 101:2008. Propagation of distributions using a Monte Carlo method.
3. IUPAC / CIAAW. Isotopic compositions of the elements (latest table).
4. AME2020 Atomic Mass Evaluation (exact isotope masses, used for mass-bias correction).

---

## 2. Background: the input data

The instrument gives a summary block for each sample and element. The current format is:

```
Yb Nat              <- element symbol + sample type/ID ("Nat" = natural)
170/173             <- ratio label: isotope 170 over isotope 173
0.185665            <- mean ratio over n = 30 measurements
334.298435          <- RSD of the 30 measurements, in ppm
171/173
0.866726
150.192199
...
```

The export format **may differ** (Q-12). Import must therefore be configurable (FR-IN-02, FR-IN-10).

¹⁶⁸Yb (≈0.12 % natural abundance) is not measured. **Decision: it is set to its natural ratio** (FR-CAL-07).

---

## 3. Users and roles

| Role | Description | Main tasks |
|---|---|---|
| **Analyst** | Runs the TIMS and enters the data | Enter/import data, review warnings, fix entry errors, submit for review, create reports |
| **Reviewer** | Senior scientist | Review data and flags, approve or reject results, create reports |
| **Administrator** | Software owner | Manage users, element library, reference compositions, lab baselines, tolerances, import formats, report headers and templates |
| **Viewer** (optional) | Manager or customer | View and download existing reports only |

Several users work at the same time from their own browsers.

---

## 4. Operating environment and constraints

| ID | Requirement | Priority |
|---|---|---|
| ENV-01 | Written in **Python 3.10 or newer**, using the **Flask** web framework. | Must |
| ENV-02 | Data stored in **SQLite**. The database runs in WAL mode so several users can read while one writes. | Must |
| ENV-03 | Hosted on the organisation's **on-premises cloud**. Reached by users through a web browser on the internal network. No internet access is needed at run time. | Must |
| ENV-04 | Works in current versions of Chrome, Edge and Firefox. No software is installed on user PCs. | Must |
| ENV-05 | Runs behind a production WSGI server (e.g. Gunicorn or Waitress) and the organisation's reverse proxy / HTTPS. Delivered with a deployment guide, and a container image (Dockerfile) if the internal cloud uses containers (Q-17). | Must |
| ENV-06 | Uses only well-maintained open-source libraries, for example: Flask, Flask-Login, Flask-WTF (forms and CSRF protection), SQLAlchemy, Jinja2, NumPy, PyYAML, and **WeasyPrint** (HTML-to-PDF). | Must |
| ENV-07 | Front-end assets (CSS/JS) are bundled with the app and do not load from public CDNs. | Must |
| ENV-08 | File storage for report JSON files and generated PDFs sits in a configured folder on the server, included in backups. | Must |
| ENV-09 | Expected load: up to about 20 named users and 5 working at the same time (to be confirmed, Q-16). SQLite is suitable at this scale. | Should |

---

## 5. Worked example (sample data from the analyst)

These calculations follow the confirmed rules: **U = 3 × SD**, ¹⁶⁸Yb assumed natural, IUPAC 2013 natural abundances. They are also the **first acceptance test** (AT-01).

### 5.1 Ratios with expanded uncertainty

SD = mean × RSD(ppm) × 10⁻⁶; **U = 3 × SD**.

| Ratio | Mean | RSD (ppm) | SD (1σ) | **U (3 × SD)** | Reported as |
|---|---|---|---|---|---|
| 170/173 | 0.185665 | 334.30 | 6.21×10⁻⁵ | 1.86×10⁻⁴ | 0.18567 ± 0.00019 |
| 171/173 | 0.866726 | 150.19 | 1.30×10⁻⁴ | 3.91×10⁻⁴ | 0.86673 ± 0.00039 |
| 172/173 | 1.334461 | 244.22 | 3.26×10⁻⁴ | 9.78×10⁻⁴ | 1.33446 ± 0.00098 |
| 174/173 | 1.947261 | 186.37 | 3.63×10⁻⁴ | 1.09×10⁻³ | 1.9473 ± 0.0011 |
| 176/173 | 0.781392 | 255.24 | 1.99×10⁻⁴ | 5.98×10⁻⁴ | 0.78139 ± 0.00060 |

### 5.2 Atom % abundance

With the reference isotope r and ratios Rᵢ = Nᵢ/Nᵣ (Rᵣ = 1):

  **Aᵢ (%) = 100 × Rᵢ / Σⱼ Rⱼ**

¹⁶⁸Yb/¹⁷³Yb is set to its natural value, 0.123/16.103 = 0.00764.

| Isotope | Measured atom % | U (3 × SD, ratios uncorrelated) | IUPAC natural atom % |
|---|---|---|---|
| ¹⁶⁸Yb | 0.1247 (assumed natural) | – | 0.123 |
| ¹⁷⁰Yb | 3.0322 | 0.0031 | 2.982 |
| ¹⁷¹Yb | 14.1549 | 0.0066 | 14.09 |
| ¹⁷²Yb | 21.7937 | 0.0133 | 21.68 |
| ¹⁷³Yb | 16.3315 | 0.0044 | 16.103 |
| ¹⁷⁴Yb | 31.8017 | 0.0137 | 32.026 |
| ¹⁷⁶Yb | 12.7613 | 0.0091 | 12.996 |
| **Sum** | **100.0000** | | 100 |

### 5.3 What the consistency check shows for this data

| Ratio | Measured | IUPAC natural ratio | Deviation |
|---|---|---|---|
| 170/173 | 0.185665 | 0.185183 | +0.26 % |
| 171/173 | 0.866726 | 0.874992 | −0.94 % |
| 172/173 | 1.334461 | 1.346333 | −0.88 % |
| 174/173 | 1.947261 | 1.988822 | −2.09 % |
| 176/173 | 0.781392 | 0.807055 | −3.18 % |

What follows from this:
1. The deviations (+0.3 % to −3.2 %) are far larger than the measurement precision (0.015–0.03 %). Comparing with IUPAC values against U would flag every ratio. The IUPAC check therefore uses wide tolerances (5 % / 20 %, accepted by the user).
2. The finer check uses the **lab baseline**, built from the 10 natural samples the lab will provide (FR-QC-11).
3. The deviations do not follow one mass-dependent trend, so a single-law mass-bias correction does not fit this data (Appendix A.3).

---

## 6. Functional requirements

Priority uses MoSCoW: **Must / Should / Could / Won't (this release)**.

### 6.1 Sample and measurement metadata (FR-META)

| ID | Requirement | Priority |
|---|---|---|
| FR-META-01 | For each measurement, capture: Sample ID, sample type (Natural / Enriched / Standard / Unknown), element, analyst, date and time of analysis, instrument ID, number of measurements n (default 30), filament/run ID, free-text remarks. For enriched samples, also capture the enriched isotope and the material/batch reference. | Must |
| FR-META-02 | Sample type must be selected explicitly. It controls which consistency checks run (Section 6.5). | Must |
| FR-META-03 | The system records the date/time and user of data entry automatically. | Must |
| FR-META-04 | Allow several runs (replicate filaments) of the same sample to be entered and combined (weighted mean, scatter between runs). | Should |
| FR-META-05 | Sample IDs are unique within an element. A duplicate ID is refused, or recorded as a new run of the existing sample if the user chooses that. | Must |

### 6.2 Data entry and import (FR-IN)

| ID | Requirement | Priority |
|---|---|---|
| FR-IN-01 | After the user selects an element, the web form shows **one row per expected ratio**, taken from the element definition. Each row has fields for **mean ratio** and **RSD (ppm)**. | Must |
| FR-IN-02 | The user can **paste text or upload a file** from the instrument. The software parses it with the selected import format and fills the form for checking before saving. | Must |
| FR-IN-03 | Import from CSV/Excel with the columns: ratio label, mean, RSD (ppm). | Should |
| FR-IN-04 | Each field is checked as it is typed and again on the server: numeric only, positive, decimal separator handled, no blank required field. | Must |
| FR-IN-05 | Ratio labels are checked: both isotopes must belong to the selected element. A ratio with a different denominator is converted automatically (FR-CAL-05). | Must |
| FR-IN-06 | Duplicate ratio labels, missing expected ratios, and unexpected extra ratios are reported. | Must |
| FR-IN-07 | The user can choose the uncertainty input type: RSD in ppm (default), RSD in %, or absolute SD. It is shown clearly on screen to prevent unit mix-ups. | Should |
| FR-IN-08 | Optional **double-entry mode**: a second person re-enters the values, and the software highlights any mismatch. | Could |
| FR-IN-09 | A form that is filled but not yet saved is kept as a draft in the browser or on the server, so it survives an accidental page reload. | Should |
| FR-IN-10 | **Configurable import formats** ("import profiles"). The Administrator defines how to read a format: line-block layout (as in Section 2) or table layout; field order; header pattern for element and sample ID; label pattern (e.g. `170/173`, `170Yb/173Yb`, `R170/173`); decimal separator; RSD unit. The current format is supplied as the default profile. New profiles are added without code changes. | Must |
| FR-IN-11 | The software tries to recognise the import profile automatically. If none matches, it lists the lines it could not read. It never silently skips data. | Should |

### 6.3 Element library (FR-EL)

| ID | Requirement | Priority |
|---|---|---|
| FR-EL-01 | Each element is defined in a **separate human-readable YAML file** (format in Section 10). New elements are added **without changing program code**. | Must |
| FR-EL-02 | The definition holds: symbol, name, atomic number, list of isotopes (mass number, exact atomic mass, natural abundance with uncertainty, data source), default reference isotope, the ratios normally measured, known isobaric interferences, and default check tolerances. | Must |
| FR-EL-03 | Yb and Lu definitions are supplied with the software (Section 10). | Must |
| FR-EL-04 | Definitions for the other lanthanides with two or more naturally occurring isotopes are supplied, or can be added: La, Ce, Nd, Sm, Eu, Gd, Dy, Er (see 6.3.1). | Should |
| FR-EL-05 | When loading a definition, the software validates it: natural abundances sum to 100 % ± 0.01, isotopes are unique, the reference isotope is in the isotope list, masses are positive, required fields are present. An invalid file is rejected with a clear message. | Must |
| FR-EL-06 | Only the Administrator can add or change element definitions (upload YAML or edit in the web page). Each change is version-stamped and logged. | Must |
| FR-EL-07 | Every result records which version of the element definition it used, so it can be reproduced. | Must |
| FR-EL-08 | More than one reference composition per element is allowed (e.g. "IUPAC 2013", "Lab baseline – natural Yb", "Enriched ¹⁷⁶Yb batch X certificate"). The checks use whichever applies (Section 6.5). | Must |
| FR-EL-09 | A blank template file and a short guide for adding new elements are provided. | Must |

#### 6.3.1 Lanthanide scope notes
- **Mono-isotopic lanthanides** (Pr, Tb, Ho, Tm) have only one natural isotope. No isotope ratio can be measured, so they cannot be used. The definition loader rejects a mono-isotopic element with an explanation.
- Some "stable" lanthanides contain very long-lived natural radioisotopes (¹³⁸La, ¹⁴⁷Sm, ¹⁴⁸Sm, ¹⁵²Gd, ¹⁷⁶Lu). At the time scales of interest this has no effect, so no decay correction is needed.
- **Neighbouring lanthanides overlap in mass.** Isobaric interference warnings (FR-QC-30) are therefore especially important. The supplied definitions list the main overlaps:

| Element | Isobaric overlaps (mass: interfering elements) |
|---|---|
| Ce | 136, 138: Ba, La · 142: Nd |
| Nd | 142: Ce · 144: Sm · 148, 150: Sm |
| Sm | 144: Nd · 148, 150: Nd · 152, 154: Gd |
| Eu | 151, 153: no isobars. Watch for oxide or hydride ions of lighter elements |
| Gd | 152, 154: Sm · 156, 158, 160: Dy |
| Dy | 156, 158, 160: Gd · 162, 164: Er |
| Er | 162, 164: Dy · 168, 170: Yb |
| Yb | 168, 170: Er · 176: Lu, Hf |
| Lu | 176: Yb, Hf |

### 6.4 Calculations (FR-CAL)

| ID | Requirement | Priority |
|---|---|---|
| FR-CAL-01 | Standard deviation of each ratio: **SD = R × RSD(ppm) × 10⁻⁶**. | Must |
| FR-CAL-02 | **Standard uncertainty u = SD** (spread of the n measurements, not divided by √n). Decided by the user (Q-01). | Must |
| FR-CAL-03 | **Expanded uncertainty U = 3 × u.** The coverage factor k = 3 is fixed by default. The Administrator can change it. The report always states "U = 3 × SD". | Must |
| FR-CAL-04 | Results show U both as an absolute value and relative value (ppm and %). | Must |
| FR-CAL-05 | Ratios can be converted to a different reference isotope: Rᵢ/ₛ = Rᵢ/ᵣ / Rₛ/ᵣ, with uncertainty propagated. | Should |
| FR-CAL-06 | **Atom % abundance**: Aᵢ = 100 × Rᵢ / Σ Rⱼ, where the sum includes the reference isotope (R = 1) and all isotopes of the element. | Must |
| FR-CAL-07 | **Isotopes not measured**: set to the natural ratio from the reference composition. For Yb this applies to ¹⁶⁸Yb (user decision Q-03). The setting is per element in the definition file. The report states which isotopes were assumed natural. For enriched samples the same rule applies unless the user enters a value from the material certificate. | Must |
| FR-CAL-08 | Uncertainty of atom % is propagated from the ratio uncertainties using the GUM first-order method: ∂Aᵢ/∂Rᵢ = (S − Rᵢ)/S², ∂Aᵢ/∂Rⱼ = −Rᵢ/S², where S = ΣR. Ratios are treated as uncorrelated by default. A correlation coefficient can be configured (Appendix A.2). Assumed-natural isotopes carry no measurement uncertainty. | Must |
| FR-CAL-09 | A **Monte Carlo** uncertainty option (JCGM 101) is available as a cross-check of FR-CAL-08. | Could |
| ~~FR-CAL-10~~ | ~~Atomic weight calculation.~~ **Removed at user request.** | – |
| FR-CAL-11 | Optionally reports **mass fraction (weight %)** of each isotope as well as atom %. | Could |
| FR-CAL-12 | Optional **mass-bias correction** with per-ratio **K-factors** from a standard measured in the same campaign (Appendix A.3). Off by default. When used, raw and corrected values are stored, and the report states the standard and K-factors used. | Should |
| FR-CAL-13 | **Optional uncertainty budget**: the Administrator may define extra uncertainty components per element and ratio (e.g. between-run reproducibility, K-factor uncertainty), expressed in ppm. These are combined with the repeatability SD as the root sum of squares before multiplying by 3 (Appendix A.1). Off by default, so U = 3 × SD as decided. | Could |
| FR-CAL-14 | **Rounding**: U is rounded to 2 significant figures. The value is rounded to the same decimal place as U. All calculations use full precision; rounding happens only on display and in reports. | Must |
| FR-CAL-15 | Calculations are deterministic and match the worked example in Section 5 to at least 6 significant figures. | Must |
| FR-CAL-16 | Combining replicate runs (FR-META-04): weighted mean, internal uncertainty, external uncertainty (scatter between runs), and the MSWD. | Should |

### 6.5 Consistency and data-entry error detection (FR-QC)

The checks run **automatically** whenever data is entered or imported, and again before results are submitted or approved. Each finding has a severity:

- **INFO**: for information only.
- **WARNING**: needs a written justification from the analyst before the data can be submitted.
- **ERROR**: blocks submission until it is fixed, or overridden by the Reviewer with a comment.

Which reference the checks compare against depends on the sample type:

| Sample type | Compared with |
|---|---|
| Natural / Standard | IUPAC natural composition (coarse) **and** lab baseline (fine) |
| Enriched | Expected composition of the enriched material (certificate or earlier accepted measurement of the same batch) |
| Unknown | Nothing pass/fail; deviation from natural shown as INFO |

#### 6.5.1 Basic validation (all sample types)

| ID | Check | Default severity | Priority |
|---|---|---|---|
| FR-QC-01 | Value is not numeric, is zero or negative, or is blank. | ERROR | Must |
| FR-QC-02 | RSD outside a plausible range (default 1 to 5,000 ppm, configurable per element and ratio). Catches RSD entered in % instead of ppm, or SD instead of RSD. | WARNING | Must |
| FR-QC-03 | Ratio label invalid, duplicated, missing or unexpected. | ERROR | Must |
| FR-QC-04 | Too few significant digits compared with what the instrument normally outputs. | INFO | Could |

#### 6.5.2 Natural and standard samples

| ID | Check | Default severity | Priority |
|---|---|---|---|
| FR-QC-10 | **Deviation from IUPAC natural ratio**: δ = (R_meas / R_nat − 1) × 100 %. Limits per element and ratio. Defaults (accepted): \|δ\| > 5 % → WARNING, \|δ\| > 20 % → ERROR. | WARNING / ERROR | Must |
| FR-QC-11 | **Comparison with the lab baseline**: z = (R_meas − mean_baseline) / SD_baseline. Defaults (accepted): \|z\| > 3 → WARNING, \|z\| > 5 → ERROR. | WARNING / ERROR | Must |
| FR-QC-12 | **Lab baseline set-up**: the Administrator loads the first **10 natural samples** per element (bulk import, or select existing approved samples). The software calculates mean, SD and number of runs per ratio. It flags any of the 10 that is itself an outlier before the baseline is accepted. Later approved natural samples can be added by the Reviewer ("include in baseline"). Every baseline version is kept, and each result records the version it was checked against. | Must |
| FR-QC-13 | Until a baseline has at least 10 runs, FR-QC-11 shows INFO only, and the screen states that the fine check is not yet active. | Must |
| FR-QC-14 | **Control chart** of each ratio over time for natural samples (±2 SD / ±3 SD lines), so drift and instrument problems can be seen. | Should |
| FR-QC-15 | **Mass-bias consistency**: estimate the fractionation factor β from each ratio (exponential law) relative to the lab baseline, and flag any ratio whose β is far from the others. A single wrong entry stands out. | Should |
| FR-QC-16 | **Abundance check**: the calculated atom % of each isotope differs from natural by more than the set tolerance. | WARNING | Must |

#### 6.5.3 Enriched samples (both natural and enriched materials are measured: user answer Q-06)

| ID | Requirement | Priority |
|---|---|---|
| FR-QC-40 | For enriched samples, natural-abundance checks (FR-QC-10/11/16) are **not** applied as pass/fail. The deviation from natural is shown as INFO. | Must |
| FR-QC-41 | The user selects or enters an **expected composition** for the enriched material: from the supplier certificate (atom % or ratios with uncertainty), or from an earlier approved measurement of the same batch. Deviation checks then run against it, with their own tolerances (default: \|δ\| > 2 % relative on the enriched isotope's ratio → WARNING, > 10 % → ERROR; configurable). | Must |
| FR-QC-42 | **Enrichment plausibility**: the isotope declared as enriched must have the highest abundance relative to natural, and the sample's composition must be "between" natural and the declared enrichment (it cannot be more depleted than natural in the enriched isotope). Catches a natural sample wrongly marked enriched, or the wrong enriched isotope chosen. | Should |
| FR-QC-43 | Basic checks (6.5.1), entry-mistake checks against the expected composition (6.5.4) and self-checks (FR-QC-32) still apply. | Must |
| FR-QC-44 | If no expected composition is given, only basic checks and the plausibility check run, and the screen says so clearly. | Must |

#### 6.5.4 Diagnosis of typical entry mistakes

When a value is flagged against its reference (natural, baseline, or expected enriched composition), the software tests common mistakes and **suggests the likely correct value**. The software never changes data without asking: the analyst must accept a suggestion, and that is logged.

| ID | Likely mistake | How it is detected | Priority |
|---|---|---|---|
| FR-QC-20 | **Decimal point shifted** (e.g. 1.947 entered as 19.47) | R × 10ᵏ (k = ±1, ±2, ±3) is within tolerance | Must |
| FR-QC-21 | **Inverted ratio** (173/170 entered instead of 170/173) | 1/R is within tolerance | Must |
| FR-QC-22 | **Values in the wrong row** (e.g. 171/173 and 172/173 swapped) | Swapping two rows brings both within tolerance | Must |
| FR-QC-23 | **Mean and RSD swapped** | Mean outside plausible range and RSD field looks like a ratio | Must |
| FR-QC-24 | **Transposed or wrong digit** (e.g. 0.185665 → 0.158665) | One adjacent-digit swap, or one changed digit, brings it within tolerance | Should |
| FR-QC-25 | **Wrong element or sample type selected** (e.g. enriched sample marked "Nat", or Lu data under Yb) | Most or all ratios fail, or they match another element or composition in the library | Should |
| FR-QC-26 | **RSD unit mix-up** (% vs ppm, SD vs RSD) | RSD × 10⁴ or RSD/R falls into the usual range for that ratio | Should |
| FR-QC-27 | **Precision does not fit counting statistics**: a ratio's RSD is far off the pattern for that run, or far off its own history | Could |

#### 6.5.5 Physics and interference checks

| ID | Check | Priority |
|---|---|---|
| FR-QC-30 | Warn about **isobaric interferences** listed in the element definition (6.3.1) when ratios involving those masses deviate while others agree. | Should |
| FR-QC-31 | Optional interference correction when an interference-monitor ratio is entered. | Could |
| FR-QC-32 | Atom % values must sum to 100 % within numerical rounding. This is an internal self-check of the software. | Must |

#### 6.5.6 How flags are presented

| ID | Requirement | Priority |
|---|---|---|
| FR-QC-50 | Flags are shown next to the field concerned, colour-coded (green / amber / red), with a plain-language explanation and, where possible, the suggested correction. | Must |
| FR-QC-51 | A summary panel lists all findings for the sample. | Must |
| FR-QC-52 | Each WARNING needs a justification comment before submission. Overriding an ERROR needs Reviewer rights and a comment. All overrides are logged and can be shown on the report. | Must |
| FR-QC-53 | All tolerances are set per element and ratio in configuration. Only the Administrator can change them, and changes are logged. | Must |

### 6.6 Results display (FR-OUT)

| ID | Requirement | Priority |
|---|---|---|
| FR-OUT-01 | Table of ratios: label, mean, RSD (ppm), SD, U (3 × SD), relative U, deviation from reference, check status. | Must |
| FR-OUT-02 | Table of atom %: isotope, abundance, U, reference abundance, deviation, status. | Must |
| FR-OUT-03 | Charts: measured vs reference abundance (bar chart); deviation per ratio with tolerance bands; control chart (FR-QC-14). | Should |
| FR-OUT-04 | Export results to CSV/Excel. | Must |

### 6.7 Data management, review, users and audit trail (FR-DATA)

| ID | Requirement | Priority |
|---|---|---|
| FR-DATA-01 | All samples, inputs, results, flags, comments, baselines and report records are stored in **SQLite**. | Must |
| FR-DATA-02 | Workflow states: **Draft → Submitted → Approved / Rejected**. Approved records are locked. Any later change creates a new version and keeps the old one. | Must |
| FR-DATA-03 | **Audit trail**: every create, change, override, approval, report generation and configuration change is recorded with user, date/time, old value, new value and reason. Ordinary users cannot edit it. | Must |
| FR-DATA-04 | Search and filter the whole database by sample ID, element, sample type, date range, analyst, status. Used both for browsing and for selecting samples for reports. | Must |
| FR-DATA-05 | **User login** with username and password (stored hashed), and role-based permissions (Section 3). Optional connection to the organisation's directory (LDAP/Active Directory) if available (Q-15). A Reviewer cannot approve their own data. | Must |
| FR-DATA-06 | Two users editing the same sample at the same time: the second save is refused with a message, so no data is silently overwritten. | Must |
| FR-DATA-07 | Session timeout after a configurable idle period (default 30 minutes). | Should |
| FR-DATA-08 | Scheduled backup of the SQLite database and the report-file folder (using SQLite's online backup), with the number of copies kept configurable. A restore procedure is documented. | Must |
| FR-DATA-09 | Export and import of the element library and import profiles, for moving between installations (e.g. test and production). | Could |

---

## 7. Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| NFR-01 | **Accuracy**: calculations agree with an independent reference calculation to ≤ 1 × 10⁻⁹ relative. | Must |
| NFR-02 | **Performance**: page response < 1 second for entry and calculation; PDF report of up to 10 samples generated in < 10 seconds. | Should |
| NFR-03 | **Usability**: an analyst can enter one Yb sample (5 ratios) and see results in under 2 minutes after 30 minutes of training. Messages use plain language. | Must |
| NFR-04 | **Reliability**: no data loss if the server restarts (database transactions, drafts saved). | Must |
| NFR-05 | **Maintainability**: the calculation and QC engine is a plain Python package with no web code, so it can be tested on its own. Flask code covers only web pages and storage. Code follows PEP 8 and has docstrings. | Must |
| NFR-06 | **Testability**: automated tests for all calculations and checks, including Section 5 and every entry-mistake type in 6.5.4. Target ≥ 90 % coverage of the calculation and QC modules. | Must |
| NFR-07 | **Traceability**: each result and report can be reproduced exactly from the stored inputs, software version, element-definition version, baseline version and configuration. | Must |
| NFR-08 | **Security**: HTTPS (through the internal reverse proxy), hashed passwords, CSRF protection on all forms, role checks on the server for every action, uploaded files size-limited and parsed as data only. | Must |
| NFR-09 | **Extensibility**: adding a new element needs only a new YAML file, with no code change. Adding an import format needs only a new import profile. | Must |
| NFR-10 | **Documentation**: user manual, administrator guide (adding elements, import profiles, baselines, report headers), deployment and backup guide, calculation-method description. | Must |
| NFR-11 | **Validation record**: a short test report showing the acceptance tests in Section 11 passed. No formal accreditation documents are needed (Q-09: no other QA rules). | Should |

---

## 8. Consistency-check logic: summary flow

```
Input (form or import profile)
   │
   ▼
Basic validation (QC-01..04) ──► fail? ──► ERROR shown, fix first
   │
   ▼
Sample type?
   ├─ Natural / Standard ──► IUPAC check (QC-10) + lab baseline check (QC-11)
   │                         + mass-bias consistency (QC-15)
   ├─ Enriched ──► expected-composition check (QC-41) + plausibility (QC-42)
   └─ Unknown ──► deviation from natural shown as INFO only
   │
   ▼
Any ratio flagged? ──► mistake diagnosis (QC-20..27)
                       ──► suggestion shown; analyst accepts or rejects (logged)
   │
   ▼
Interference checks (QC-30) ──► abundance and sum checks (QC-16, QC-32)
   │
   ▼
Summary ──► justify WARNINGs / Reviewer overrides ERRORs ──► Submit ──► Approve
```

---

## 9. Reporting (FR-REP)

### 9.1 Workflow

1. The user opens **"New report"** and searches the **whole database** (FR-DATA-04). Any sample can be chosen, from any element, date or analyst. One or more samples can be selected.
2. The user chooses:
   - **Divisional header** (from the list set up by the Administrator),
   - **Table content**: **Isotope ratios ± U** *or* **Atom % ± U**,
   - **Layout**: Format A (one sample per table) or Format B (several samples side by side), see 9.3,
   - names and designations for the **two signature blocks**,
   - **Remarks** (free text).
3. The user clicks **Preview**, which shows the report on screen with a "PREVIEW" watermark.
4. The user clicks **Generate**. The software:
   - gives the report a **unique Report ID**,
   - saves the **report JSON** file (9.5) and a record in the database,
   - creates the **PDF**.
5. Anyone with access can later open the report list, download the PDF, or **recreate the PDF from the JSON** (9.6).

### 9.2 Requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-REP-01 | The user can select **any sample(s) from the complete database** for a report, using search and filters. | Must |
| FR-REP-02 | Only **Approved** samples appear in a final report. If an unapproved sample is selected, the report is marked "DRAFT – NOT APPROVED" on every page (Q-13). | Must |
| FR-REP-03 | **Divisional header** at the top of every page. The Administrator maintains one or more header profiles: organisation name, division name, section/laboratory name, address line, logo image, and an optional document/form number. The user picks one per report. | Must |
| FR-REP-04 | **Measurement table**, with the content **chosen by the user**: (a) isotope ratios with U, or (b) atom % with U. One choice per report. | Must |
| FR-REP-05 | Below the table, fixed **notes** generated automatically: "U = 3 × SD (n = 30 measurements)"; reference isotope; isotopes assumed natural (e.g. "¹⁶⁸Yb assumed natural"); mass-bias correction applied or not; reference composition used for checks. | Must |
| FR-REP-06 | **Two signature placeholders** after the table and notes, side by side: "Prepared by" and "Checked / Approved by", each with name, designation and date. The labels can be changed by the Administrator. | Must |
| FR-REP-07 | **Remarks** area below the signatures, filled with the text entered by the report creator. Shown as "Remarks: Nil" if left empty. | Must |
| FR-REP-08 | Last line of the report body: **"This report is digitally generated and does not need signatures."** The wording can be changed by the Administrator. | Must |
| FR-REP-09 | **Unique Report ID** on every page. Proposed format: `TIMS/<DIV>/<YYYY>/<NNNN>`, for example `TIMS/ACD/2026/0007`. `<DIV>` comes from the header profile; `<NNNN>` is a running number reset each year. IDs are never reused, even if a report is cancelled. | Must |
| FR-REP-10 | Each generated report is stored as a **JSON file** named after the Report ID (slashes replaced, e.g. `TIMS_ACD_2026_0007.json`), and registered in the database. The generated PDF is also stored. | Must |
| FR-REP-11 | **Recreate PDF from JSON**: the software reads a stored report JSON, fetches the referenced sample versions from the database, and produces the PDF again with the same content and layout. A report JSON can also be uploaded (e.g. from an archive) to recreate its PDF, as long as the samples exist in the database. | Must |
| FR-REP-12 | **Integrity check on recreation**: the JSON holds a snapshot of the reported values and a checksum. On recreation the software compares them with the database. If a sample has since been changed, deleted or re-versioned, it warns and shows the differences. The original stored values are used for the recreated PDF, so it always matches the issued report. | Must |
| FR-REP-13 | A report, once generated, cannot be edited. A correction is a **new revision**: same Report ID with a suffix (`…/0007-R1`), a reason for revision, and a reference to the earlier revision. Both are kept. | Must |
| FR-REP-14 | Page footer: Report ID, revision, "Page x of y", date/time generated, software version. | Must |
| FR-REP-15 | Reports use HTML templates (Jinja2) rendered to PDF (WeasyPrint), so the Administrator can adjust layout later without code changes. Each report JSON records the template version used. | Must |
| FR-REP-16 | Report list page: search by Report ID, sample ID, date, creator; download PDF or JSON; recreate PDF; create revision. | Must |
| FR-REP-17 | Option to include the QC findings and overrides as an annex page. | Could |
| FR-REP-18 | Option to export the report table to Excel. | Could |

### 9.3 Proposed report formats (for the user to correct)

A4 portrait, 20 mm margins. Values ± U, rounded per FR-CAL-14.

#### Format A: one sample per table (atom % shown)

```
┌──────────────────────────────────────────────────────────────────────┐
│ [LOGO]   <ORGANISATION NAME>                                          │
│          <Division name>                                              │
│          <Section / Laboratory name> · <Address line>                 │
├──────────────────────────────────────────────────────────────────────┤
│              ISOTOPIC COMPOSITION REPORT (TIMS)                       │
│ Report ID : TIMS/ACD/2026/0007            Date of issue : 06-11-2026  │
├──────────────────────────────────────────────────────────────────────┤
│ Sample ID : YB-NAT-01        Element   : Ytterbium (Yb)               │
│ Sample type: Natural         Analysed  : 18-10-2026                   │
│ Instrument : TIMS-1          Analyst   : <name>                       │
├──────────────────────────────────────────────────────────────────────┤
│  Isotope │   Atom %   │  U (3 × SD) │                                 │
│  ────────┼────────────┼─────────────┤                                 │
│  ¹⁶⁸Yb   │   0.1247 * │      –      │                                 │
│  ¹⁷⁰Yb   │   3.0322   │   0.0031    │                                 │
│  ¹⁷¹Yb   │  14.1549   │   0.0066    │                                 │
│  ¹⁷²Yb   │  21.794    │   0.013     │                                 │
│  ¹⁷³Yb   │  16.3315   │   0.0044    │                                 │
│  ¹⁷⁴Yb   │  31.802    │   0.014     │                                 │
│  ¹⁷⁶Yb   │  12.7613   │   0.0091    │                                 │
│  Total   │ 100.000    │             │                                 │
├──────────────────────────────────────────────────────────────────────┤
│ Notes: U = 3 × SD, n = 30. Ratios measured against ¹⁷³Yb.             │
│        * ¹⁶⁸Yb not measured; natural abundance assumed.               │
│        No mass-bias correction applied.                               │
│                                                                       │
│   Prepared by                         Checked / Approved by           │
│   ______________________              ______________________          │
│   <Name>                              <Name>                          │
│   <Designation>                       <Designation>                   │
│   Date: __________                    Date: __________                │
│                                                                       │
│ Remarks:                                                              │
│ <text entered by the report creator>                                  │
│                                                                       │
│ This report is digitally generated and does not need signatures.      │
├──────────────────────────────────────────────────────────────────────┤
│ TIMS/ACD/2026/0007 · Rev 0 · Generated 06-11-2026 14:32 · Page 1 of 1 │
└──────────────────────────────────────────────────────────────────────┘
```

With several samples, Format A repeats the sample block and table for each sample. The signatures, remarks and final statement appear once, after the last table.

#### Format A, ratio option: the table becomes

```
│  Ratio      │   Value    │  U (3 × SD) │  RSD (ppm) │
│  ¹⁷⁰Yb/¹⁷³Yb │  0.18567   │  0.00019    │    334     │
│  ¹⁷¹Yb/¹⁷³Yb │  0.86673   │  0.00039    │    150     │
│  ¹⁷²Yb/¹⁷³Yb │  1.33446   │  0.00098    │    244     │
│  ¹⁷⁴Yb/¹⁷³Yb │  1.9473    │  0.0011     │    186     │
│  ¹⁷⁶Yb/¹⁷³Yb │  0.78139   │  0.00060    │    255     │
```

#### Format B: several samples side by side (same element)

Best for batches, e.g. a natural standard reported with enriched samples. Up to 4 samples per table (portrait) or 6 (landscape). More samples continue in a second table.

```
│ Isotope │  YB-NAT-01       │  YB-ENR-176-03   │  YB-ENR-176-04   │
│         │  Atom % ± U      │  Atom % ± U      │  Atom % ± U      │
│ ¹⁶⁸Yb   │  0.1247*         │  …               │  …               │
│ ¹⁷⁰Yb   │  3.0322 ± 0.0031 │  …               │  …               │
│ …       │  …               │  …               │  …               │
│ Sample type │ Natural      │  Enriched        │  Enriched        │
│ Analysed    │ 18-10-2026   │  …               │  …               │
```

**Recommendation:** use Format A as the default. Offer Format B for batches of one element. Mixed elements in one report always use Format A.

### 9.4 Divisional header profile (Administrator setting)

```yaml
code: ACD                       # used in the Report ID
organisation: "<Organisation name>"
division: "<Division name>"
section: "<Section / Laboratory name>"
address: "<Address line>"
logo: logos/acd.png
form_number: "<optional document/form no.>"
signature_labels: ["Prepared by", "Checked / Approved by"]
footer_statement: "This report is digitally generated and does not need signatures."
```

### 9.5 Report JSON structure

```json
{
  "schema_version": 1,
  "report_id": "TIMS/ACD/2026/0007",
  "revision": 0,
  "supersedes": null,
  "revision_reason": null,
  "created_at": "2026-11-06T14:32:10+05:30",
  "created_by": "analyst1",
  "software_version": "1.0.0",
  "template": {"name": "format_a", "version": "1.0"},
  "header_profile": {"code": "ACD", "version": 3},
  "content": "atom_percent",
  "layout": "format_a",
  "samples": [
    {
      "sample_id": "YB-NAT-01",
      "element": "Yb",
      "record_version": 2,
      "status_at_report": "Approved",
      "element_definition_version": "2026-10-19.1",
      "snapshot": {
        "reference_isotope": 173,
        "assumed_natural": [168],
        "ratios": [
          {"label": "170/173", "value": 0.185665, "rsd_ppm": 334.298435, "U": 0.000186204}
        ],
        "atom_percent": [
          {"isotope": 170, "value": 3.032223, "U": 0.003147}
        ]
      }
    }
  ],
  "signatories": [
    {"label": "Prepared by", "name": "<Name>", "designation": "<Designation>"},
    {"label": "Checked / Approved by", "name": "<Name>", "designation": "<Designation>"}
  ],
  "remarks": "<text entered by report creator>",
  "footer_statement": "This report is digitally generated and does not need signatures.",
  "checksum_sha256": "<hash of all fields above>"
}
```

(The snapshot is shortened here; the real file holds every ratio and isotope.)

### 9.6 Recreating a PDF from JSON

1. Read the JSON and check the checksum. A file that has been altered is refused.
2. For each sample, find `sample_id` + `record_version` in the database.
3. Compare the database values with the snapshot. Any differences are shown to the user (FR-REP-12).
4. Render the PDF with the template version and header profile version named in the JSON. If those versions are not available, use the current ones and add the note "Recreated with template version X".
5. Record the recreation in the audit trail. The Report ID does not change.

---

## 10. Element definition file format

One YAML file per element in `elements/`. All values must be checked against the latest IUPAC/CIAAW and AME2020 tables before release.

### 10.1 Ytterbium

```yaml
schema_version: 1
element:
  symbol: Yb
  name: Ytterbium
  atomic_number: 70
  definition_version: "2026-11-06.1"

reference_isotope: 173
measured_ratios: [170, 171, 172, 174, 176]   # numerators normally measured
unmeasured_isotopes: assume_natural           # 168Yb (user decision Q-03)

isotopes:            # A: mass number, mass (u), natural abundance (atom %), 1-sigma unc.
  - {A: 168, mass: 167.933891, abundance: 0.123,  u_abundance: 0.003}
  - {A: 170, mass: 169.934767, abundance: 2.982,  u_abundance: 0.039}
  - {A: 171, mass: 170.936331, abundance: 14.09,  u_abundance: 0.14}
  - {A: 172, mass: 171.936386, abundance: 21.68,  u_abundance: 0.13}
  - {A: 173, mass: 172.938216, abundance: 16.103, u_abundance: 0.063}
  - {A: 174, mass: 173.938867, abundance: 32.026, u_abundance: 0.080}
  - {A: 176, mass: 175.942574, abundance: 12.996, u_abundance: 0.083}
abundance_source: "IUPAC/CIAAW 2013"

interferences:
  - {mass: 168, species: ["168Er"]}
  - {mass: 170, species: ["170Er"]}
  - {mass: 176, species: ["176Lu", "176Hf"]}

qc_tolerances:
  natural_deviation_pct: {warning: 5, error: 20}
  baseline_z:            {warning: 3, error: 5}
  enriched_deviation_pct: {warning: 2, error: 10}
  rsd_ppm:               {min: 1, max: 5000}
```

### 10.2 Lutetium

```yaml
schema_version: 1
element:
  symbol: Lu
  name: Lutetium
  atomic_number: 71
  definition_version: "2026-11-06.1"

reference_isotope: 175
measured_ratios: [176]            # 176/175, natural ≈ 0.02668
unmeasured_isotopes: assume_natural

isotopes:
  - {A: 175, mass: 174.940777, abundance: 97.401, u_abundance: 0.013}
  - {A: 176, mass: 175.942691, abundance: 2.599,  u_abundance: 0.013}
abundance_source: "IUPAC/CIAAW 2013"

interferences:
  - {mass: 176, species: ["176Yb", "176Hf"]}

qc_tolerances:
  natural_deviation_pct: {warning: 5, error: 20}
  baseline_z:            {warning: 3, error: 5}
  enriched_deviation_pct: {warning: 2, error: 10}
  rsd_ppm:               {min: 1, max: 5000}
```

> **Note on Lu:** there is only one ratio. Checks that compare ratios with each other (QC-15, QC-22) do not apply. The natural comparison, lab baseline and single-value mistake checks (QC-20, 21, 23, 24, 26) cover it. Interference from ¹⁷⁶Yb is the main risk.

### 10.3 Import profile (example of the current format)

```yaml
name: "TIMS summary block (default)"
layout: line_block            # line_block | table
header_pattern: '^(?P<element>[A-Z][a-z]?)\s+(?P<sample>.+)$'
group: [label, mean, rsd]     # repeating group of lines
label_pattern: '^(?P<num>\d+)\s*/\s*(?P<den>\d+)$'
rsd_unit: ppm
decimal_separator: "."
```

---

## 11. Acceptance criteria

| ID | Test | Expected result |
|---|---|---|
| AT-01 | Paste the Yb Nat sample data (Section 2) | Parsed correctly. Ratios, U (= 3 × SD) and atom % match Section 5 to 6 significant figures. ¹⁶⁸Yb assumed natural. |
| AT-02 | Enter 174/173 = 19.47261 | ERROR, with "decimal point shifted?" suggestion 1.947261. |
| AT-03 | Enter 170/173 = 5.386 (≈ 1/0.185665) | ERROR, with "inverted ratio?" suggestion 0.18567. |
| AT-04 | Swap the 171/173 and 172/173 values | Both flagged, with suggestion to swap rows. |
| AT-05 | Enter RSD = 0.0334 (% instead of ppm) | WARNING about RSD range and possible unit mix-up. |
| AT-06 | Enter 334.298435 as the 170/173 mean | ERROR, with "mean and RSD swapped?" suggestion. |
| AT-07 | Enriched ¹⁷⁶Yb sample with a certificate composition | Checked against the certificate, not natural. A natural sample marked enriched is caught by QC-42. |
| AT-08 | Add Nd from a YAML file only | Nd appears in the list and works end to end without code change. |
| AT-09 | Load an invalid definition (abundances sum ≠ 100 %, or a mono-isotopic element) | Rejected with a clear message. |
| AT-10 | Change an approved result | A new version is created, and the change is in the audit trail. |
| AT-11 | Lu: 176/175 = 0.02668, RSD 300 ppm | Atom % ≈ 97.40 / 2.60 with correct U. No flags. |
| AT-12 | Load 10 natural Yb samples as baseline | Baseline mean/SD per ratio calculated. QC-11 becomes active. |
| AT-13 | Import a file in a new format after adding an import profile | Parsed correctly without code change. |
| AT-14 | Two users save the same sample at the same time | The second save is refused with a message. No data lost. |
| AT-15 | Generate a report for 2 samples (atom %), with header, signatures and remarks | PDF matches Format A. Unique Report ID. JSON stored. Final statement present. |
| AT-16 | Same samples, ratio option | Table shows ratios ± U. |
| AT-17 | Delete the PDF and recreate it from the JSON | Recreated PDF has the same content as the original. |
| AT-18 | Change a sample after reporting, then recreate the PDF | Warning lists the differences. Recreated PDF shows the originally reported values. |
| AT-19 | Edit a stored JSON by hand and try to recreate it | Refused (checksum fails). |

---

## 12. Open questions

### 12.1 Answered (v0.1 questions)

| ID | Answer | Effect on this URD |
|---|---|---|
| Q-01 | U = 3 × SD (Section 5.1) | FR-CAL-02/03 |
| Q-02 | Explanation requested | Appendix A.1. FR-CAL-13 added as an optional feature |
| Q-03 | ¹⁶⁸Yb assumed natural | FR-CAL-07 |
| Q-04 | Explanation requested | Appendix A.2. Default stays "uncorrelated" |
| Q-05 | Explanation requested | Appendix A.3. FR-CAL-12 changed to K-factor method |
| Q-06 | Both natural and enriched | Section 6.5.3 made Must |
| Q-07 | Flask + SQLite web app, multi-user, on-premises cloud | Section 4, FR-DATA-05..08 |
| Q-08 | 10 baseline samples to start | FR-QC-12/13 |
| Q-09 | No other QA rules | ISO 17025 items removed; NFR-11 simplified |
| Q-10 | Stable lanthanides | Section 6.3.1 |
| Q-11 | Default tolerances accepted | Section 6.5 / 10 |
| Q-12 | Export format may differ | FR-IN-10/11, import profiles |

### 12.2 New questions

| ID | Question | Why it matters |
|---|---|---|
| Q-13 | Can **unapproved** samples be put in a report (marked DRAFT), or must the software block them completely? | FR-REP-02 |
| Q-14 | The report has two **signature placeholders** and also says it **does not need signatures**. Should the placeholders print only the names and designations (no lines to sign on)? | Layout of FR-REP-06 |
| Q-15 | User accounts: local accounts in the app, or login through the organisation's directory (LDAP/Active Directory)? | FR-DATA-05 |
| Q-16 | About how many users in total, and how many at the same time? | Confirms SQLite is enough (ENV-09) |
| Q-17 | Does the internal cloud run containers (Docker/Kubernetes) or plain virtual machines? | Deployment package (ENV-05) |
| Q-18 | Please send **sample export files** in each format the instrument(s) produce. | Import profiles (FR-IN-10) |
| Q-19 | Division names, codes, logos and the preferred Report ID format. | FR-REP-03/09 |
| Q-20 | After reading Appendix A: do you want the optional uncertainty budget (FR-CAL-13) and K-factor mass-bias correction (FR-CAL-12) in the first release? Which reference material would be used for K-factors? | Scope of release 1 |
| Q-21 | For enriched samples, are supplier certificates available as atom % or as ratios? | FR-QC-41 entry form |

---

## 13. Future enhancements (not in the first release)

- Reading raw cycle data from instrument files, with outlier rejection, and calculation of ratio correlations from it (Appendix A.2).
- Isotope dilution (IDMS) concentration calculation.
- Multi-instrument comparison.
- LIMS integration.
- Electronic signatures.

---

## 14. Proposed software structure

```
tims_reporting/
├── app/                      # Flask application
│   ├── __init__.py           # app factory, config, extensions
│   ├── auth/                 # login, users, roles
│   ├── samples/              # data entry, import, review/approve pages
│   ├── baselines/            # lab baseline management, control charts
│   ├── reports/              # report builder, list, recreate from JSON
│   ├── admin/                # elements, import profiles, tolerances, headers
│   ├── models.py             # SQLAlchemy models (SQLite)
│   ├── templates/            # Jinja2 HTML pages + report templates
│   └── static/               # CSS/JS, bundled (no CDN)
├── tims_core/                # calculation engine (no web code)
│   ├── parsing.py            # import profiles
│   ├── elements.py           # load + validate element YAML
│   ├── calc.py               # SD, U, atom %, propagation, Monte Carlo
│   ├── qc.py                 # all checks + mistake diagnosis
│   └── report_json.py        # build / verify report JSON
├── elements/                 # yb.yaml, lu.yaml, nd.yaml, ...
├── import_profiles/          # default.yaml, ...
├── instance/                 # SQLite database, report JSON + PDF files (not in git)
├── tests/                    # unit + acceptance tests (Section 11)
├── docs/                     # this URD, manuals
├── Dockerfile                # if containers are used (Q-17)
└── wsgi.py                   # entry point for Gunicorn/Waitress
```

Build order proposed:
1. `tims_core`: calculations and QC, tested against Section 5 and Section 11.
2. Flask app: login, data entry, import, review.
3. Lab baseline.
4. Reporting.

---

## Appendix A: Explanations requested by the user

### A.1 Uncertainty budget (Q-02): how other components can be included

**What U = 3 × SD covers today.** The SD from the instrument is the **repeatability** of one filament run: how much the 30 measurements scatter. It does not include effects that are the same for all 30 measurements, or that only show when the sample is run again on another filament or day.

**Other components that can be added**, each as a standard uncertainty in ppm:

| Component | What it is | How it is obtained |
|---|---|---|
| u_rep: repeatability | Scatter of the 30 measurements (current SD) | From the instrument (RSD) |
| u_R: reproducibility (between runs) | Scatter between different filaments/days for the same material | SD of the lab baseline (10 natural samples) minus the typical in-run SD, or from replicate runs |
| u_K: mass-bias correction | Uncertainty of the K-factor (only if correction is applied) | From the standard's certificate and its measured SD |
| u_int: interference correction | Uncertainty of any correction for isobars | From the monitor-ratio measurement |
| u_blank | Contribution from the filament/chemistry blank | Usually negligible for TIMS at normal loads; from blank measurements |

**How they are combined** (GUM): standard uncertainties that are independent are added as the **root sum of squares**, then multiplied by 3:

  u_c = √(u_rep² + u_R² + u_K² + …)  U = 3 × u_c

**Example for 170/173**, with illustrative values. Suppose repeatability is 334 ppm (measured), reproducibility from the baseline is 500 ppm, and K-factor uncertainty is 100 ppm:

  u_c = √(334² + 500² + 100²) ≈ 610 ppm → U = 3 × 0.185665 × 610 × 10⁻⁶ ≈ 0.00034

This compares with 0.00019 from repeatability alone.

**How it would work in the software (FR-CAL-13, optional):** the Administrator enters the extra components per element and ratio, in ppm. Reproducibility can be filled in automatically from the lab baseline. The software combines them as above. The report notes then state the components used. If the feature is off, U = 3 × SD exactly as decided.

**Recommendation:** keep U = 3 × SD for release 1 as decided. Once the 10 baseline samples are loaded, the software will show the between-run scatter. If it is much larger than the in-run SD, consider switching on the reproducibility component.

### A.2 Correlation between ratios (Q-04)

**Why ratios are correlated.** All Yb ratios are measured against the **same isotope, ¹⁷³Yb**. If the ¹⁷³Yb signal is randomly a bit high in a cycle, *every* ratio in that cycle is a bit low at the same time. So the errors of 170/173, 171/173, … move together: they are **positively correlated**. The correlation coefficient ρ is between 0 (independent) and 1 (fully linked).

**Why it matters.** It does **not** change the ratios or their individual uncertainties. It changes only the **uncertainty of the atom %**, because atom % combines all ratios. The current calculation assumes ρ = 0. For your Yb data, U (3 × SD) of atom % for different assumed ρ:

| Isotope | ρ = 0 (current) | ρ = 0.5 | ρ = 0.9 |
|---|---|---|---|
| ¹⁷⁰Yb | 0.0031 | 0.0024 | 0.0017 |
| ¹⁷¹Yb | 0.0066 | 0.0047 | 0.0023 |
| ¹⁷²Yb | 0.0133 | 0.0099 | 0.0060 |
| ¹⁷³Yb | 0.0044 | 0.0069 | 0.0083 |
| ¹⁷⁴Yb | 0.0137 | 0.0097 | 0.0044 |
| ¹⁷⁶Yb | 0.0091 | 0.0068 | 0.0041 |

Positive correlation makes the atom % uncertainty **smaller** for most isotopes but **larger for the reference isotope ¹⁷³Yb**. Ignoring correlation can therefore overstate some uncertainties and understate one, by up to about a factor of 2.

**How ρ can be obtained.** It is calculated from the **cycle-by-cycle data** (all 30 individual ratios). The summary block (mean + RSD) does not contain it.

**Recommendation for release 1:** keep ρ = 0 (current default), and state "ratios treated as uncorrelated" in the report notes. The software will allow a fixed ρ per element if the lab decides on one. Once the instrument can export cycle data, the software can calculate ρ directly (Section 13).

### A.3 Mass-bias correction (Q-05): how it is done

**What mass bias is.** In TIMS, the sample evaporates from a hot filament. **Lighter isotopes evaporate slightly faster** than heavier ones. So the measured ratio of a light to a heavy isotope starts a little high and drifts down as the sample is used up. The ion optics and detectors add their own small mass-dependent effects. The result is that measured ratios differ from the true ratios by an amount that grows with the mass difference. It is typically 0.1–1 % per mass unit for lanthanides.

**Method 1: external correction with a standard (K-factor).** Recommended for this lab, because enriched samples are measured.
1. Measure a standard of known composition (a certified reference material, or a well-characterised natural Yb) under the same conditions as the samples.
2. For each ratio: **K = R_certified / R_measured,standard**.
3. Correct each sample ratio: **R_corrected = K × R_measured,sample**.
4. The uncertainty of K is added to the sample's uncertainty (Appendix A.1, u_K).

This works for both natural and enriched samples, because K describes the instrument, not the sample. One K per ratio also handles the case where the bias does not follow a simple law.

**Method 2: internal normalisation.** One ratio of the sample is assumed to have its natural value (e.g. 176/173 or 172/173). Its deviation gives a fractionation factor β, and β corrects all other ratios using a mass law, for example the exponential law:

  R_true = R_meas × (mᵢ / mᵣ)^β

This is common for natural samples in geochemistry. It **cannot be used for enriched samples**, because no ratio in an enriched sample has its natural value.

**What your sample data shows.** Normalising on 176/173 gives β ≈ 1.88. Applying it to 170/173 moves that ratio from 0.18567 to 0.17966. Its natural value is 0.18518, so the correction makes 170/173 *worse*, not better. A single mass law does not explain the deviations in this data (Section 5.3), so per-ratio K-factors (Method 1) are the safer choice.

**Method 3: total evaporation.** The whole sample is evaporated and all ions are summed, which largely cancels the time-dependent part of the bias. If your instrument uses this method, a K-factor correction may still remove the remaining bias.

**Recommendation:** release 1 stores and reports the **uncorrected** ratios, as now. It includes the K-factor correction as an option the Administrator can switch on per element (FR-CAL-12), and the report states whether a correction was applied. The lab must choose the standard to use (Q-20). The IUPAC natural values themselves have about 0.5–1 % uncertainty for some Yb isotopes, so a certified isotopic reference material gives a better K-factor than "natural Yb = IUPAC".
