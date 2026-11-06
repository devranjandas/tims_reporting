#!/usr/bin/env bash
# Install or upgrade TIMS-Report on a Linux virtual machine (RHEL/Rocky/Alma 8+ or Ubuntu 22.04+).
# Run as root from the unpacked application folder:   sudo bash deploy/install.sh
# The application goes to /opt/tims-report and its data to /var/lib/tims-report.
set -euo pipefail

APP_DIR=/opt/tims-report
DATA_DIR=/var/lib/tims-report
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "== System packages (Python 3.10+, and Pango for PDF output)"
if command -v dnf >/dev/null; then
  dnf install -y python3.11 python3.11-pip pango rsync
  PY=python3.11
elif command -v apt-get >/dev/null; then
  apt-get update
  apt-get install -y rsync python3 python3-venv python3-pip libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core
  PY=python3
else
  echo "Unsupported system: install Python 3.10+ and Pango by hand." >&2
  exit 1
fi

echo "== Service user and folders"
id tims >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /sbin/nologin tims
mkdir -p "$APP_DIR" "$DATA_DIR/backups"

echo "== Application files"
# Element definitions and import formats may have been edited in the app: keep them on upgrade.
for d in elements import_profiles; do
  if [ -d "$APP_DIR/$d" ]; then cp -a "$APP_DIR/$d" "$DATA_DIR/$d.before-upgrade.$(date +%Y%m%d%H%M%S)"; fi
done
rsync -a --delete --exclude .venv --exclude instance --exclude .git --exclude elements --exclude import_profiles \
  "$SRC_DIR/" "$APP_DIR/"
for d in elements import_profiles; do
  if [ ! -d "$APP_DIR/$d" ]; then cp -a "$SRC_DIR/$d" "$APP_DIR/$d"; fi
done

echo "== Python environment"
[ -d "$APP_DIR/.venv" ] || "$PY" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "== Configuration"
if [ ! -f /etc/tims-report.env ]; then
  install -m 600 "$APP_DIR/deploy/tims-report.env.example" /etc/tims-report.env
fi
chown -R tims:tims "$DATA_DIR" "$APP_DIR/elements" "$APP_DIR/import_profiles"

echo "== Services"
install -m 644 "$APP_DIR/deploy/tims-report.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/tims-report-backup.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/tims-report-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tims-report.service tims-report-backup.timer
systemctl restart tims-report.service

echo
echo "Installed. Next steps:"
echo "  1. Create the first administrator:"
echo "       sudo -u tims bash -c 'set -a; . /etc/tims-report.env; cd $APP_DIR && .venv/bin/flask --app wsgi create-admin'"
echo "  2. Set up nginx with deploy/nginx-tims-report.conf and your internal HTTPS certificate."
echo "  3. Open https://<server name>/ and log in."
