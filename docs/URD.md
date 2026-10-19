# User Requirements Document (URD)
## TIMS Isotopic Composition Reporting Software ("TIMS-Report")

| Item | Value |
|---|---|
| Document ID | TIMS-URD-001 |
| Version | 0.1 (Draft for review) |
| Date | 2026-10-19 |
| Status | Draft: needs answers to the open questions in Section 12 |
| Initial elements | Ytterbium (Yb), Lutetium (Lu) |

---

## 1. Introduction

### 1.1 Purpose
This document sets out what users need from software that takes isotope ratio data measured on a Thermal Ionisation Mass Spectrometer (TIMS) and:

1. reports each measured isotope ratio with its **expanded uncertainty (k = 3, "3σ")**,
2. works out the **isotopic composition (atom %)** of the element from those ratios, with uncertainties,
3. **finds inconsistent or wrongly entered data** by comparing it with natural isotopic abundances and with the lab's own past measurements,
4. supports **new elements** added through a standard definition file, without code changes,
5. produces a **report**. The report layout and content will be specified later (see Section 9).

### 1.2 Scope
In scope:
- Manual entry and text import of TIMS summary results (mean ratio + RSD in ppm).
- Uncertainty calculation and propagation.
- Calculation of atom % abundance (and, optionally, atomic weight).
- Element library (Yb, Lu first, extensible).
- Data consistency and plausibility checks.
- Storing results, keeping an audit trail, and generating reports.

Out of scope for now (may come later, see Section 13):
- Direct control of, or live acquisition from, the TIMS instrument.
- Processing raw cycle-by-cycle data, including outlier rejection inside the 30 cycles.
- Isotope dilution (IDMS) concentration calculations.

### 1.3 Definitions and abbreviations

| Term | Meaning |
|---|---|
| TIMS | Thermal Ionisation Mass Spectrometry |
| Ratio, Rᵢ/ᵣ | Number of atoms of isotope *i* divided by atoms of reference isotope *r* (e.g. ¹⁷⁰Yb/¹⁷³Yb) |
| Reference (denominator) isotope | Isotope all ratios are expressed against (¹⁷³Yb in the sample data) |
| n | Number of measurements (cycles/blocks) averaged by the instrument (30 in the sample data) |
| RSD | Relative standard deviation = SD / mean |
| ppm | Parts per million (1 ppm = 10⁻⁶) |
| SD | Standard deviation of the n individual measurements |
| SE | Standard error of the mean = SD / √n |
| k | Coverage factor; k = 3 gives the "3σ" expanded uncertainty requested |
| U | Expanded uncertainty = k × standard uncertainty |
| Atom % | Fraction of all atoms of the element that belong to one isotope, × 100 |
| Natural abundance | Representative isotopic composition of the element in nature (IUPAC / CIAAW) |
| Nat | Sample label meaning "natural (not enriched) material" |
| Mass fractionation / mass bias | Instrument effect where measured ratios drift from true values, depending on the mass difference |
| Isobaric interference | An isotope of another element with the same mass number (e.g. ¹⁷⁶Lu, ¹⁷⁶Hf on ¹⁷⁶Yb) |
| GUM | ISO/IEC Guide 98-3, *Guide to the Expression of Uncertainty in Measurement* |

### 1.4 References
1. JCGM 100:2008 (GUM). Evaluation of measurement data: guide to the expression of uncertainty in measurement.
2. JCGM 101:2008. Propagation of distributions using a Monte Carlo method.
3. IUPAC / CIAAW. Isotopic compositions of the elements (latest table, currently 2013 with later updates).
4. AME2020 Atomic Mass Evaluation (exact isotope masses).
5. ISO/IEC 17025:2017. General requirements for the competence of testing and calibration laboratories (if the lab is accredited or plans to be).

---

## 2. Background: the input data

The instrument gives a summary block for each sample and element:

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

The pattern repeats: **header line**, then groups of three lines: **ratio label**, **mean**, **RSD (ppm)**.

Notes on the sample data:
- ¹⁶⁸Yb (≈0.12 % natural abundance) is **not measured**. The software must decide how to handle isotopes that were not measured (FR-CAL-07).
- One label in the original data reads "174/173". It was checked against the other entries and is consistent.

---

## 3. Users and roles

| Role | Description | Main tasks |
|---|---|---|
| **Analyst** | Runs the TIMS and enters the data | Enter/import data, review warnings, fix entry errors, submit for review |
| **Reviewer / Checker** | Senior scientist | Review data and flags, approve or reject results, sign off reports |
| **Administrator** | Lab QA or software owner | Manage element library, reference values, tolerances, users, report templates |
| **Viewer** (optional) | Customer or manager | View and print approved reports only |

---

## 4. Operating environment and constraints

| ID | Requirement | Priority |
|---|---|---|
| ENV-01 | Written in **Python 3.10 or newer**. | Must |
| ENV-02 | Runs on Windows 10/11 lab PCs. Linux support desirable. | Must |
| ENV-03 | Works **fully offline**. TIMS lab PCs are often not connected to the internet. | Must |
| ENV-04 | Uses only well-maintained open-source libraries (e.g. NumPy, pandas, PyYAML, a GUI toolkit, a PDF/Excel library). | Must |
| ENV-05 | Can be installed as a single package or installer, without the user installing Python separately. | Should |
| ENV-06 | Type of user interface (desktop GUI or local web app) to be confirmed (Q-07). | – |

---

## 5. Worked example (sample data from the analyst)

This example shows the calculations the software must perform. It also serves as the **first acceptance test**. Values assume the "SD of the 30 measurements" interpretation (see Q-01) and IUPAC 2013 natural abundances.

### 5.1 Ratios with expanded uncertainty (k = 3)

SD = mean × RSD(ppm) × 10⁻⁶; U(k=3) = 3 × SD.

| Ratio | Mean | RSD (ppm) | SD (1σ) | **U (3σ)** | Reported as | If SE used: U = 3·SD/√30 |
|---|---|---|---|---|---|---|
| 170/173 | 0.185665 | 334.30 | 6.21×10⁻⁵ | 1.86×10⁻⁴ | 0.18567 ± 0.00019 | 3.4×10⁻⁵ |
| 171/173 | 0.866726 | 150.19 | 1.30×10⁻⁴ | 3.91×10⁻⁴ | 0.86673 ± 0.00039 | 7.1×10⁻⁵ |
| 172/173 | 1.334461 | 244.22 | 3.26×10⁻⁴ | 9.78×10⁻⁴ | 1.33446 ± 0.00098 | 1.8×10⁻⁴ |
| 174/173 | 1.947261 | 186.37 | 3.63×10⁻⁴ | 1.09×10⁻³ | 1.9473 ± 0.0011 | 2.0×10⁻⁴ |
| 176/173 | 0.781392 | 255.24 | 1.99×10⁻⁴ | 5.98×10⁻⁴ | 0.78139 ± 0.00060 | 1.1×10⁻⁴ |

### 5.2 Atom % abundance

With the reference isotope r and ratios Rᵢ = Nᵢ/Nᵣ (Rᵣ = 1):

  **Aᵢ (%) = 100 × Rᵢ / Σⱼ Rⱼ**

¹⁶⁸Yb was not measured. Here it is set to its natural ratio (0.123/16.103 = 0.00764) for illustration.

| Isotope | Measured atom % | U (3σ, ratios uncorrelated) | IUPAC natural atom % |
|---|---|---|---|
| ¹⁶⁸Yb | 0.1247 (assumed) | – | 0.123 |
| ¹⁷⁰Yb | 3.0322 | 0.0031 | 2.982 |
| ¹⁷¹Yb | 14.1549 | 0.0066 | 14.09 |
| ¹⁷²Yb | 21.7937 | 0.0133 | 21.68 |
| ¹⁷³Yb | 16.3315 | 0.0044 | 16.103 |
| ¹⁷⁴Yb | 31.8017 | 0.0137 | 32.026 |
| ¹⁷⁶Yb | 12.7613 | 0.0091 | 12.996 |
| **Sum** | **100.0000** | | 100 |

Atomic weight from these abundances: ≈ 173.041 (IUPAC standard atomic weight of Yb: 173.045).

### 5.3 What the consistency check shows for this data

| Ratio | Measured | IUPAC natural ratio | Deviation |
|---|---|---|---|
| 170/173 | 0.185665 | 0.185183 | **+0.26 %** |
| 171/173 | 0.866726 | 0.874992 | **−0.94 %** |
| 172/173 | 1.334461 | 1.346333 | **−0.88 %** |
| 174/173 | 1.947261 | 1.988822 | **−2.09 %** |
| 176/173 | 0.781392 | 0.807055 | **−3.18 %** |

Observations that drive the requirements in Section 8:
1. The deviations (+0.3 % to −3.2 %) are much larger than the measurement precision (0.015–0.03 %). Comparing with IUPAC values against the measurement uncertainty would flag **every** ratio.
2. The deviations do not follow one simple mass-dependent trend. For example, 170/173 is high while 171/173 and 172/173 are low. Possible causes include uncorrected mass bias, the uncertainty in the IUPAC values themselves (±1 % for ¹⁷¹Yb), and interference on ¹⁷⁶ (¹⁷⁶Lu/¹⁷⁶Hf).
3. So the natural-abundance check needs **graded tolerances** and a **lab-specific baseline**, built from the lab's own repeated measurements of natural Yb on the same instrument. A single fixed IUPAC comparison is not enough. Typical entry mistakes (decimal shift, swapped digits, inverted ratio, values in the wrong row) produce deviations far larger than 3 %, so they can still be caught reliably.

---

## 6. Functional requirements

Priority uses MoSCoW: **Must / Should / Could / Won't (this release)**.

### 6.1 Sample and measurement metadata (FR-META)

| ID | Requirement | Priority |
|---|---|---|
| FR-META-01 | For each measurement, capture: Sample ID, sample type (Natural / Enriched / Standard / Unknown), element, analyst, date and time of analysis, instrument ID, number of measurements n (default 30), filament/run ID, free-text remarks. | Must |
| FR-META-02 | Sample type must be selected explicitly. It controls which consistency checks run (natural-abundance checks apply to Natural and Standard samples only). | Must |
| FR-META-03 | The system records the date/time and user of data entry automatically. | Must |
| FR-META-04 | Allow several runs (replicate filaments) of the same sample to be entered and combined (weighted mean, scatter between runs). | Should |

### 6.2 Data entry (FR-IN)

| ID | Requirement | Priority |
|---|---|---|
| FR-IN-01 | After the user selects an element, the software shows an entry form with **one row per expected ratio**, taken from the element definition (e.g. 170/173, 171/173, 172/173, 174/173, 176/173 for Yb). Each row has fields for **mean ratio** and **RSD (ppm)**. | Must |
| FR-IN-02 | The user can **paste or import the instrument text block** in the format shown in Section 2. The software parses it and fills the form. | Must |
| FR-IN-03 | Import from CSV/Excel with the columns: ratio label, mean, RSD (ppm). | Should |
| FR-IN-04 | The software checks each field as it is typed: numeric only, positive, decimal separator handled, no blank required field. | Must |
| FR-IN-05 | Ratio labels are checked: both isotopes must belong to the selected element, and the denominator must match the element's reference isotope. Different reference isotopes may be allowed by configuration and converted automatically (FR-CAL-05). | Must |
| FR-IN-06 | Duplicate ratio labels, missing expected ratios, and unexpected extra ratios are reported. | Must |
| FR-IN-07 | The user can choose the uncertainty input type: RSD in ppm (default), RSD in %, or absolute SD. It is shown clearly on screen to prevent unit mix-ups. | Should |
| FR-IN-08 | Optional **double-entry mode**: a second person (or the same analyst again) re-enters the values, and the software highlights any mismatch. | Could |
| FR-IN-09 | Data that is entered but not yet saved is kept as a draft if the application closes. | Should |

### 6.3 Element library (FR-EL)

| ID | Requirement | Priority |
|---|---|---|
| FR-EL-01 | Each element is defined in a **separate human-readable definition file** (YAML or JSON, format in Section 10). New elements can be added **without changing program code**. | Must |
| FR-EL-02 | The definition holds: symbol, name, atomic number, list of isotopes (mass number, exact atomic mass, natural abundance with uncertainty, data source), default reference isotope, the set of ratios normally measured, known isobaric interferences, and default check tolerances. | Must |
| FR-EL-03 | Yb and Lu definitions are supplied with the software (values in Section 10). | Must |
| FR-EL-04 | When loading a definition, the software validates it: natural abundances sum to 100 % ± 0.01, isotopes are unique, the reference isotope is in the isotope list, masses are positive, required fields are present. An invalid file is rejected with a clear message. | Must |
| FR-EL-05 | Only the Administrator can add or edit element definitions through the user interface. Each change is version-stamped and logged. | Must |
| FR-EL-06 | Every result records which version of the element definition it used, so it can be reproduced. | Must |
| FR-EL-07 | More than one reference composition per element is allowed (e.g. "IUPAC 2013", "Lab in-house natural Yb standard", "Certified reference material X"). The user or Administrator picks the one used for checks. | Should |
| FR-EL-08 | A blank template file and a short guide for adding new elements are provided. | Must |

### 6.4 Calculations (FR-CAL)

| ID | Requirement | Priority |
|---|---|---|
| FR-CAL-01 | Standard deviation of each ratio: **SD = R × RSD(ppm) × 10⁻⁶**. | Must |
| FR-CAL-02 | Standard uncertainty of the mean ratio: either u = SD or u = SD/√n, as set in the configuration (see Q-01). The setting is printed on every report. | Must |
| FR-CAL-03 | **Expanded uncertainty U = k × u, with k = 3 by default.** k is configurable (e.g. k = 2 for 95 %), and the value used is always printed. | Must |
| FR-CAL-04 | Results show U both as an absolute value and relative value (ppm and %). | Must |
| FR-CAL-05 | Ratios can be converted to a different reference isotope: Rᵢ/ₛ = Rᵢ/ᵣ / Rₛ/ᵣ, with uncertainty propagated. | Should |
| FR-CAL-06 | **Atom % abundance**: Aᵢ = 100 × Rᵢ / Σ Rⱼ, where the sum includes the reference isotope (R = 1) and all other isotopes of the element. | Must |
| FR-CAL-07 | **Isotopes not measured** (e.g. ¹⁶⁸Yb): the user/Administrator chooses per element how to handle them: (a) assume the natural ratio from the reference composition (default for natural samples), (b) enter a value manually, or (c) leave out and report "sum of measured isotopes". The chosen option is printed on the report. | Must |
| FR-CAL-08 | Uncertainty of atom % is propagated from the ratio uncertainties using the GUM first-order (linear) method: ∂Aᵢ/∂Rᵢ = (S − Rᵢ)/S², ∂Aᵢ/∂Rⱼ = −Rᵢ/S², where S = ΣR. Correlations between ratios are ignored by default. Correlations can be supplied when available (Q-04). | Must |
| FR-CAL-09 | A **Monte Carlo** uncertainty option (JCGM 101, e.g. 10⁶ trials) is available as a cross-check of FR-CAL-08. | Could |
| FR-CAL-10 | Optionally calculates the **atomic weight** of the element in the sample, with uncertainty, from the abundances and exact isotope masses. | Should |
| FR-CAL-11 | Optionally reports **mass fraction (weight %)** of each isotope as well as atom %. | Could |
| FR-CAL-12 | Optional **mass-bias correction** with exponential/linear/power law, using either a known ratio of a standard or an internal normalisation ratio. Off by default. Raw and corrected values are both shown. (Confirm whether needed, Q-05.) | Could |
| FR-CAL-13 | **Rounding**: U is rounded to 2 significant figures. The value is rounded to the same decimal place as U. All calculations use full precision; rounding happens only on display and in reports. | Must |
| FR-CAL-14 | Calculations are deterministic and match the worked example in Section 5 to at least 6 significant figures. | Must |
| FR-CAL-15 | Combining replicate runs (FR-META-04): weighted mean, internal (propagated) uncertainty, external uncertainty (scatter between runs), and the MSWD/Birge ratio. | Should |

### 6.5 Consistency and data-entry error detection (FR-QC)

This is a key feature. The checks run **automatically** on every entry/import, and again before results are saved or approved. Each finding has a severity:

- **INFO**: for information only.
- **WARNING**: needs a written justification from the analyst before the data can be submitted.
- **ERROR**: blocks submission until it is fixed, or overridden by the Reviewer with a comment.

#### 6.5.1 Basic validation

| ID | Check | Default severity | Priority |
|---|---|---|---|
| FR-QC-01 | Value is not numeric, is zero or negative, or is blank. | ERROR | Must |
| FR-QC-02 | RSD is outside a plausible range (default 1 to 5,000 ppm, configurable per element and ratio). Catches RSD entered in % instead of ppm, or SD instead of RSD. | WARNING | Must |
| FR-QC-03 | Ratio label invalid, duplicated, missing or unexpected (FR-IN-05/06). | ERROR | Must |
| FR-QC-04 | Too many or too few significant digits compared with what the instrument normally outputs (e.g. a mean with only 2 decimals). | INFO | Could |

#### 6.5.2 Comparison with natural abundance (Natural / Standard samples)

| ID | Check | Default severity | Priority |
|---|---|---|---|
| FR-QC-10 | **Relative deviation from the natural ratio**: δ = (R_meas / R_nat − 1) × 100 %. Two configurable limits per element and ratio. Proposed defaults: |δ| > 5 % → WARNING, |δ| > 20 % → ERROR. These must be wider than the deviations seen in good data (up to 3.2 % in Section 5.3). | WARNING / ERROR | Must |
| FR-QC-11 | **Comparison with the lab baseline**: compare each ratio with the mean and SD of the lab's previous accepted measurements of natural material for the same element and instrument. Flag when the z-score is beyond ±3 (WARNING) or ±5 (ERROR). This is far more sensitive than FR-QC-10 because it removes the lab's own systematic bias. | WARNING / ERROR | Must |
| FR-QC-12 | **Control chart** of each ratio over time for natural standards (Shewhart-type, with ±2σ/±3σ lines), so trends, drift and instrument problems can be seen. | Should |
| FR-QC-13 | **Mass-bias consistency**: estimate the fractionation factor β from each ratio (exponential law: β = ln(R_meas/R_nat) / ln(mᵢ/mᵣ)) and flag any ratio whose β differs strongly from the others. A single wrong entry stands out from the common trend. Enabled once a lab baseline exists, because IUPAC values alone are not consistent enough (Section 5.3). | Should |
| FR-QC-14 | **Abundance check**: the calculated atom % of each isotope differs from natural by more than the set tolerance (absolute and relative). | WARNING | Must |

#### 6.5.3 Diagnosis of typical entry mistakes

When FR-QC-10 or FR-QC-11 flags a value, the software tests common mistakes and **suggests the likely correct value**. The software never changes data without asking: the analyst must accept a suggestion, and that is logged.

| ID | Likely mistake | How it is detected | Priority |
|---|---|---|---|
| FR-QC-20 | **Decimal point shifted** (e.g. 1.947 entered as 19.47 or 0.1947) | R × 10ᵏ (k = ±1, ±2, ±3) is within tolerance of natural | Must |
| FR-QC-21 | **Inverted ratio** (173/170 entered instead of 170/173) | 1/R is within tolerance of natural | Must |
| FR-QC-22 | **Values in the wrong row** (e.g. 171/173 and 172/173 values swapped) | Swapping two rows brings both within tolerance | Must |
| FR-QC-23 | **Mean and RSD swapped** (e.g. 334.298 entered as the ratio) | Mean outside plausible range and RSD field looks like a ratio | Must |
| FR-QC-24 | **Transposed or wrong digit** (e.g. 0.185665 → 0.158665) | Value fails check, and a single swap of adjacent digits, or a change of one digit, brings it within tolerance | Should |
| FR-QC-25 | **Wrong element or wrong sample type selected** (e.g. enriched sample marked "Nat", or Lu data under Yb) | Most or all ratios fail, or the ratios match another element/composition in the library | Should |
| FR-QC-26 | **RSD unit mix-up** (% vs ppm, SD vs RSD) | RSD × 10⁴ or RSD/R falls into the typical range for that ratio | Should |
| FR-QC-27 | **Precision does not fit counting statistics**: RSD should roughly grow as the minor-isotope abundance falls. Flag a ratio whose RSD is far off the pattern for that run, or far off its own history. | Could |

#### 6.5.4 Physics and interference checks

| ID | Check | Priority |
|---|---|---|
| FR-QC-30 | Warn about known **isobaric interferences** listed in the element definition (e.g. ¹⁷⁶Lu and ¹⁷⁶Hf on ¹⁷⁶Yb; ¹⁶⁸Er and ¹⁷⁰Er on ¹⁶⁸Yb/¹⁷⁰Yb; ¹⁷⁶Yb and ¹⁷⁶Hf on ¹⁷⁶Lu) when the ratios involving those masses deviate while others agree. | Should |
| FR-QC-31 | Optional interference correction when an interference-monitor ratio is entered (e.g. ¹⁷⁴Yb or ¹⁷³Yb measured with Lu to correct ¹⁷⁶Yb on ¹⁷⁶Lu). | Could |
| FR-QC-32 | Atom % values must sum to 100 % within numerical rounding. This is an internal self-check of the software. | Must |

#### 6.5.5 Enriched and unknown samples

| ID | Requirement | Priority |
|---|---|---|
| FR-QC-40 | For Enriched or Unknown samples, natural-abundance checks are **not** applied as pass/fail. The deviation from natural is still shown for information. | Must |
| FR-QC-41 | For enriched samples, the user can enter an **expected composition** (e.g. from the supplier certificate) and the same deviation checks run against it. | Should |
| FR-QC-42 | Basic checks (6.5.1), entry-mistake checks against the expected composition, and internal self-checks still apply. | Must |

#### 6.5.6 How flags are presented

| ID | Requirement | Priority |
|---|---|---|
| FR-QC-50 | Flags are shown next to the field concerned, colour-coded (green / amber / red), with a plain-language explanation and, where possible, the suggested correction. | Must |
| FR-QC-51 | A summary panel lists all findings for the sample. | Must |
| FR-QC-52 | Each WARNING needs a justification comment before submission. Overriding an ERROR needs Reviewer rights and a comment. All overrides are logged and shown on the report. | Must |
| FR-QC-53 | All tolerances are set per element and per ratio in configuration. Only the Administrator can change them, and changes are logged. | Must |

### 6.6 Results display (FR-OUT)

| ID | Requirement | Priority |
|---|---|---|
| FR-OUT-01 | Table of ratios: label, mean, RSD (ppm), SD, U (k), relative U, deviation from natural, check status. | Must |
| FR-OUT-02 | Table of atom % (and optionally weight %): isotope, abundance, U, natural abundance, deviation, status. | Must |
| FR-OUT-03 | Optional charts: measured vs natural abundance (bar chart); deviation per ratio with tolerance bands; control chart (FR-QC-12). | Should |
| FR-OUT-04 | Copy results to the clipboard, or export to CSV/Excel. | Must |

### 6.7 Reporting (FR-REP): to be fully specified later

| ID | Requirement | Priority |
|---|---|---|
| FR-REP-01 | Generate a report for one sample, or a batch, as **PDF**, and optionally **Excel/Word**. | Must |
| FR-REP-02 | Reports are built from **editable templates**, so layout and content can be decided later without code changes. | Must |
| FR-REP-03 | As a minimum, the report can contain: lab/report header, sample metadata, input data, ratios with U, atom % with U, uncertainty method (u = SD or SE, k), reference composition used, handling of unmeasured isotopes, QC findings and overrides, analyst and reviewer names with dates, software version, element-definition version. | Must |
| FR-REP-04 | Only **approved** results can produce a final report. Draft reports are watermarked "DRAFT". | Must |
| FR-REP-05 | Unique report number. A revised report keeps the original number with a revision suffix and a reason for the change. | Should |
| FR-REP-06 | Detailed report structure and wording | **Deferred (to be specified with the user)** |

### 6.8 Data management, review and audit trail (FR-DATA)

| ID | Requirement | Priority |
|---|---|---|
| FR-DATA-01 | All samples, inputs, results, flags and comments are stored in a local database (e.g. SQLite) that can be backed up. | Must |
| FR-DATA-02 | Workflow states: **Draft → Submitted → Approved / Rejected**. Approved records are locked. Any later change creates a new version and keeps the old one. | Must |
| FR-DATA-03 | **Audit trail**: every create, change, override, approval and configuration change is recorded with user, date/time, old value, new value and reason. The audit trail cannot be edited. | Must |
| FR-DATA-04 | Search and filter past results by sample ID, element, date, analyst, status. | Must |
| FR-DATA-05 | Accepted natural-standard results automatically feed the lab baseline (FR-QC-11) when the Reviewer marks them "include in baseline". | Should |
| FR-DATA-06 | User login with roles (Section 3). A Reviewer cannot approve their own data. | Should |
| FR-DATA-07 | Export and import of the whole database, or the element library, for transfer between PCs. | Could |

---

## 7. Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| NFR-01 | **Accuracy**: calculations agree with an independent reference calculation (spreadsheet or hand calculation) to ≤ 1 × 10⁻⁹ relative. | Must |
| NFR-02 | **Performance**: calculation and checks for one sample finish in < 1 second. A report is generated in < 5 seconds. Monte Carlo (10⁶ trials) finishes in < 30 seconds. | Should |
| NFR-03 | **Usability**: an analyst can enter one Yb sample (5 ratios) and get results in under 2 minutes after 30 minutes of training. Messages use plain language. | Must |
| NFR-04 | **Reliability**: no data loss on crash or power cut (database transactions, autosave of drafts). | Must |
| NFR-05 | **Maintainability**: calculation engine kept separate from the user interface and reporting, so it can be tested on its own. Code follows PEP 8 and has docstrings. | Must |
| NFR-06 | **Testability**: automated unit tests for all calculations and checks, including the worked example in Section 5 and each entry-mistake type in 6.5.3. Target ≥ 90 % coverage of the calculation and QC modules. | Must |
| NFR-07 | **Traceability**: each result can be reproduced exactly from the stored inputs, software version, element-definition version and configuration. | Must |
| NFR-08 | **Validation**: a validation/verification document (test cases and results) is produced, suitable for an ISO/IEC 17025 audit. | Should |
| NFR-09 | **Security**: user passwords stored hashed. Configuration and element library editable only by the Administrator. | Should |
| NFR-10 | **Extensibility**: adding a new element needs only a new definition file plus optional tolerance settings, with no code change (FR-EL-01). | Must |
| NFR-11 | **Documentation**: user manual, administrator guide (including how to add an element) and calculation-method description. | Must |

---

## 8. Consistency-check logic: summary flow

```
Input ──► Basic validation (QC-01..04) ──► fail? ──► ERROR shown, stop
   │
   ▼
Sample type?
   ├─ Natural/Standard ──► compare with natural (QC-10)
   │                        compare with lab baseline (QC-11)
   │                        mass-bias consistency (QC-13)
   ├─ Enriched with expected composition ──► compare with expected (QC-41)
   └─ Unknown ──► informational deviation only (QC-40)
   │
   ▼
Any ratio flagged? ──► run mistake diagnosis (QC-20..27)
                       ──► suggest correction, analyst accepts or rejects (logged)
   │
   ▼
Interference checks (QC-30) ──► Abundance and sum checks (QC-14, QC-32)
   │
   ▼
Summary of findings ──► justify WARNINGs / Reviewer overrides ERRORs ──► Submit
```

---

## 9. Reporting: placeholder

The user has said that report structure and contents will be specified later. For now the software design must:
- keep report generation in a separate module driven by templates (FR-REP-02),
- make all data from FR-REP-03 available to templates.

A separate *Report Specification* document will follow.

---

## 10. Element definition file format

A YAML file per element in a folder such as `elements/`. Proposed format (all values to be verified against the latest IUPAC/CIAAW and AME2020 tables before release):

### 10.1 Ytterbium

```yaml
schema_version: 1
element:
  symbol: Yb
  name: Ytterbium
  atomic_number: 70
  definition_version: "2026-10-19.1"

reference_isotope: 173            # denominator for ratios
measured_ratios: [170, 171, 172, 174, 176]   # numerators normally measured
unmeasured_isotope_policy: assume_natural    # assume_natural | manual | exclude

isotopes:            # mass number: exact mass (u), natural abundance (atom %), 1-sigma unc.
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

qc_tolerances:          # defaults; can be overridden per ratio
  natural_deviation_pct: {warning: 5, error: 20}
  baseline_z:            {warning: 3, error: 5}
  rsd_ppm:               {min: 1, max: 5000}
```

### 10.2 Lutetium

```yaml
schema_version: 1
element:
  symbol: Lu
  name: Lutetium
  atomic_number: 71
  definition_version: "2026-10-19.1"

reference_isotope: 175
measured_ratios: [176]            # ratio 176/175, natural ≈ 0.02668
unmeasured_isotope_policy: assume_natural

isotopes:
  - {A: 175, mass: 174.940777, abundance: 97.401, u_abundance: 0.013}
  - {A: 176, mass: 175.942691, abundance: 2.599,  u_abundance: 0.013}
abundance_source: "IUPAC/CIAAW 2013"

interferences:
  - {mass: 176, species: ["176Yb", "176Hf"]}

qc_tolerances:
  natural_deviation_pct: {warning: 5, error: 20}
  baseline_z:            {warning: 3, error: 5}
  rsd_ppm:               {min: 1, max: 5000}
```

> **Note on Lu:** with only two isotopes there is a single ratio. Checks that compare ratios with each other (QC-13, QC-22) do not apply, so the natural comparison, lab baseline and entry-mistake checks (QC-20, 21, 23, 24, 26) carry the load. Interference from ¹⁷⁶Yb is the main risk to the ¹⁷⁶Lu/¹⁷⁵Lu ratio.

---

## 11. Acceptance criteria (initial)

| ID | Test | Expected result |
|---|---|---|
| AT-01 | Enter or paste the Yb Nat sample data (Section 2) | Parsed correctly. Ratios, U (k=3) and atom % match Section 5 to 6 significant figures. No ERROR flags. Deviations shown as in 5.3. |
| AT-02 | Enter 174/173 = 19.47261 | ERROR, with "decimal point shifted?" suggestion 1.947261. |
| AT-03 | Enter 170/173 = 5.386 (≈ 1/0.185665) | ERROR, with "inverted ratio?" suggestion 0.18567. |
| AT-04 | Swap the 171/173 and 172/173 values | Both flagged, with suggestion to swap rows. |
| AT-05 | Enter RSD = 0.0334 (i.e. % instead of ppm) | WARNING about RSD range and possible unit mix-up. |
| AT-06 | Enter 334.298435 as the 170/173 mean | ERROR, with "mean and RSD swapped?" suggestion. |
| AT-07 | Mark an enriched sample as Enriched | No natural-deviation ERRORs. Deviations shown as information. |
| AT-08 | Add a new element (e.g. Nd or Sr) from a definition file only | Element appears in the list and works end to end without code change. |
| AT-09 | Load an invalid definition file (abundances sum ≠ 100 %) | Rejected with a clear message. |
| AT-10 | Change an approved result | Blocked, or a new version is created, and the change is in the audit trail. |
| AT-11 | Lu: enter 176/175 = 0.02668 with RSD 300 ppm | Correct atom % (≈ 97.40 / 2.60) and U. No flags. |

---

## 12. Open questions for the user

| ID | Question | Why it matters |
|---|---|---|
| **Q-01** | The RSD from the instrument is the scatter of the 30 measurements. For the **reported mean**, should the uncertainty be based on **SD** (as in Section 5.1) or on the **standard error SD/√30** (about 5.5× smaller)? Does the instrument software already report the RSD of the mean? | Changes every reported uncertainty by a factor of √30. |
| Q-02 | Should the expanded uncertainty include other components besides measurement repeatability (e.g. mass-bias correction, reference-material uncertainty, blank, between-run reproducibility)? | Defines a full uncertainty budget (GUM) versus precision only. |
| Q-03 | How should **¹⁶⁸Yb** (not measured) be handled: assumed natural, entered manually, or excluded? Does this apply to enriched samples too? | Affects the atom % of all Yb isotopes slightly (≈ 0.12 %). |
| Q-04 | Should the correlation between ratios that share the same denominator (¹⁷³Yb) be taken into account? Can the instrument export correlation coefficients or cycle data? | Affects the uncertainty of atom %. |
| Q-05 | Are the instrument ratios already **mass-bias corrected**? If not, should the software correct them, and with which method/standard? | Explains the deviations in Section 5.3. Affects the natural check and the results. |
| Q-06 | Which samples are measured: only natural, or also **enriched** isotopes (e.g. ¹⁷⁶Yb for ¹⁷⁷Lu production)? | Decides how important the enriched-sample features (6.5.5) are. |
| Q-07 | Desktop application (e.g. PySide6/Qt) or local web application (e.g. browser-based, runs on the lab PC)? Single PC or shared on the lab network? | Architecture and installation. |
| Q-08 | How many past measurements of natural Yb and Lu are available to build the **lab baseline** (FR-QC-11)? In what form? | Without a baseline only the coarser IUPAC check is possible at first. |
| Q-09 | Is the lab ISO/IEC 17025 accredited, or are there other QA rules (e.g. internal QA manual) the software must follow? | Audit trail, validation and e-signature depth. |
| Q-10 | Which elements are likely to be added next (e.g. Nd, Sm, Gd, Sr, U, Pu, B, Li)? | Check that the definition format covers them (e.g. elements with many isotopes, or radioactive ones that need decay correction). |
| Q-11 | Are the proposed default tolerances acceptable (5 % warning / 20 % error vs natural; z = 3/5 vs baseline)? | Balance between catching errors and false alarms. |
| Q-12 | Exact instrument export format: always exactly as in Section 2, or are there other variants (extra columns, different labels, per-block data)? | Parser design. |

---

## 13. Future enhancements (not in first release)

- Report structure and templates (separate specification).
- Reading raw cycle data directly from instrument export files, with outlier rejection (e.g. 2σ/3σ filter).
- Isotope dilution (IDMS) concentration calculation.
- Decay correction for radioactive isotopes (e.g. ¹⁷⁶Lu half-life ≈ 3.7×10¹⁰ y is negligible here, but it matters for other elements).
- Multi-instrument support and comparison between instruments.
- LIMS integration.

---

## 14. Proposed software structure (for discussion)

```
tims_reporting/
├── elements/            # element definition files (yb.yaml, lu.yaml, ...)
├── config/              # tolerances, uncertainty settings, report templates
├── tims_report/
│   ├── parsing.py       # instrument text / CSV import
│   ├── elements.py      # load + validate element definitions
│   ├── calc.py          # uncertainty, atom %, atomic weight, Monte Carlo
│   ├── qc.py            # all consistency checks + mistake diagnosis
│   ├── storage.py       # database, versioning, audit trail
│   ├── reporting.py     # template-based report generation
│   └── ui/              # user interface
├── tests/               # unit + acceptance tests (Section 11)
└── docs/                # this URD, user manual, validation report
```

The calculation and QC modules will be built and tested first, using the worked example. The user interface and reporting will follow once the open questions are answered.
