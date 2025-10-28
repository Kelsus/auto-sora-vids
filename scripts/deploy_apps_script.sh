#!/usr/bin/env bash
set -euo pipefail

# Deploys the Google Apps Script project defined in docs/apps_script/Code.gs.
# Requires @google/clasp to be installed globally and the user to be logged in
# (`clasp login --creds <service-account.json>` or via browser).

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
APP_SCRIPT_DIR="${PROJECT_ROOT}/docs/apps_script"

if ! command -v clasp >/dev/null 2>&1; then
  echo "Error: @google/clasp not installed. Run 'npm install -g @google/clasp'." >&2
  exit 1
fi

if [[ ! -f "${APP_SCRIPT_DIR}/.clasp.json" ]]; then
  echo "Error: Missing .clasp.json in ${APP_SCRIPT_DIR}. Initialize with 'clasp create --title <title> --type standalone'." >&2
  exit 1
fi

cd "${APP_SCRIPT_DIR}"

clasp push --force
clasp deploy --description "Automated deploy $(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "Deployment completed. Check the Apps Script project for the new version."
