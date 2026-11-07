# TIMS-Report

A web application for TIMS isotope ratio data. You enter or paste the instrument's mean ratios and
RSD (ppm). The application then:

- reports each ratio with its expanded uncertainty, **U = 3 × SD**,
- calculates the **atom %** of every isotope, with uncertainties,
- **checks the data** against natural abundance, the lab's own baseline, or an earlier measurement of
  the same enriched batch, and suggests corrections for typical typing mistakes,
- handles review and approval, and keeps a full audit trail,
- generates **PDF reports** with a unique ID. Each report is stored as a JSON file that can recreate
  the PDF.

The requirements are in [docs/URD.md](docs/URD.md). Built with Python, Flask and SQLite.

## Project layout

| Folder | Contents |
|---|---|
| `tims_core/` | Calculation and checking engine (plain Python, no web code) |
| `app/` | Flask web application: pages, database tables, PDF reports |
| `elements/` | One YAML file per element (`yb.yaml`, `lu.yaml`, `_template.yaml`) |
| `import_profiles/` | Instrument text formats the importer understands |
| `deploy/` | Install script, systemd service, backup timer, nginx example |
| `tests/` | Engine and web tests, including the URD acceptance tests |
| `instance/` | Created at run time: database, report files, history (not in git) |

## Try it on your own computer

```bash
python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
flask --app wsgi create-admin   # asks for username, name and password
flask --app wsgi run            # open http://127.0.0.1:5000
```

PDF output uses WeasyPrint, which needs the **Pango** system library. `pip` cannot install it. Without
it, the app still runs, but making a report shows "PDF output not available".

| System | Install Pango with |
|---|---|
| Ubuntu / Debian | `sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core` |
| RHEL / Rocky / Alma | `sudo dnf install -y pango` |
| macOS | `brew install pango` |
| Windows | Install [MSYS2](https://www.msys2.org) to `C:\msys64`, run `pacman -S mingw-w64-x86_64-pango` in the "MSYS2 MINGW64" terminal, then set the environment variable `WEASYPRINT_DLL_DIRECTORIES=C:\msys64\mingw64\bin` |

Run the tests with `pytest`.

## Install on a virtual machine

On a Linux VM (RHEL/Rocky/Alma 8+ or Ubuntu 22.04+), from the unpacked application folder:

```bash
sudo bash deploy/install.sh
```

This script:
1. installs Python and Pango,
2. creates a `tims` service user,
3. copies the application to `/opt/tims-report` and puts its data in `/var/lib/tims-report`,
4. starts the `tims-report` service (Gunicorn on 127.0.0.1:8000),
5. turns on a daily backup at 01:30.

At the end it prints the command to create the first administrator. Then put nginx in front, using
`deploy/nginx-tims-report.conf` and a certificate from your internal certificate authority.

Settings live in `/etc/tims-report.env` (see `deploy/tims-report.env.example`). Run the same script
again to upgrade. Element definitions and import formats edited in the app are kept.

**Backups.** `flask --app wsgi backup` writes one `.tar.gz` file holding:
- a consistent copy of the database (safe to take while users are working),
- all report JSON and PDF files,
- the element definitions and import formats.

The last 14 backups are kept. To restore, stop the service, unpack a backup, and put `tims.db` and the
`reports/` folder back in `/var/lib/tims-report`.

## Daily use

1. **New sample.** Paste the instrument text and press *Read text*, or type the values. Then press
   *Save and check*.
2. **Checks.** Each check gives one of three results:
   - **ERROR** blocks submission. Fix the value, or a reviewer overrides it with a reason.
   - **WARNING** needs a written justification.
   - **INFO** is for information only.

   Where a typing mistake is likely, *Apply this correction* fixes it. Every applied correction is
   logged.
3. **Submit**, then a **reviewer** (not the person who entered the data) approves or rejects it.
   Approved samples are locked; *Create new version* keeps the approved copy in the history.
4. **Lab baselines.** Load your 10 natural samples (use *Bulk import*) and get them approved. Then
   select them on the *Lab baselines* page. The fine check against the baseline starts at 10 runs.
5. **New report.** Pick any samples, type or load a divisional header, choose atom % or ratios and
   Format A or B, then fill in the two names and the remarks.
   - *Preview PDF* shows the report first.
   - *Generate report* gives it a Report ID such as `TIMS/ACD/2026/0007` and stores its JSON and PDF.
   - A report that includes any unapproved sample is marked DRAFT on every page.
6. **Recreate or revise.** In the report list:
   - *Recreate* checks the JSON and draws the PDF again, warning you if any sample has changed since.
   - *Revise* issues the same ID with `-R1`, `-R2` and a reason.
   - Any report JSON can also be uploaded to recreate its PDF.

### Adding an element

Go to *Admin → Elements → Add new*. The template opens with every field explained. When you save,
the file is checked:
- the natural abundances must add up to 100 %,
- the reference isotope must be in the isotope list,
- the element must have at least two isotopes,
- the tolerance limits must be valid.

Change `definition_version` whenever you edit a file. Each result records the version it used.

### Adding an import format

Go to *Admin → Import formats*. Two layouts are supported:
- **line blocks**, like the current TIMS output,
- **tables** with a delimiter.

You set the column order, the decimal separator and the RSD unit, with no code change.

## Release 1 status

Built:
- every **Must** requirement in the URD,
- most of the **Should** requirements.

Not built yet (all **Should** or **Could** in the URD):

- Combining replicate runs of one sample (FR-META-04, FR-CAL-16).
- Monte Carlo uncertainty (FR-CAL-09) and weight % (FR-CAL-11).
- Double-entry mode (FR-IN-08).
- RSD pattern check (FR-QC-27) and interference correction (FR-QC-31).
- A bar chart on the sample page (FR-OUT-03); the control charts are built.
- A QC annex and Excel export of reports (FR-REP-17/18). Sample results can be exported to CSV.
- Mass-bias correction (FR-CAL-12): left out of release 1 by user decision.
