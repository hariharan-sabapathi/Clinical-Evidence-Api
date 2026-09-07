#!/usr/bin/env bash
# Downloads the 120-patient Synthea FHIR bulk export this project is built against
# and stages the resource files the pipeline reads into data/raw/.
#
# Source: smart-on-fhir/sample-bulk-fhir-datasets, branch "100-patients"
# (named for the Synthea generation run size; the actual export contains 120
# patients — see docs/corpus-properties.md).
set -euo pipefail

REPO_URL="https://github.com/smart-on-fhir/sample-bulk-fhir-datasets"
BRANCH="100-patients"
DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data/raw"
TMP_CLONE="$(mktemp -d)"

RESOURCES=(
  Patient Encounter DocumentReference Condition MedicationRequest
  Procedure Observation DiagnosticReport Immunization AllergyIntolerance
)

echo "Cloning ${REPO_URL} (branch ${BRANCH}) ..."
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$TMP_CLONE"

mkdir -p "$DEST"
for r in "${RESOURCES[@]}"; do
  for f in "$TMP_CLONE/$r".*.ndjson; do
    [ -e "$f" ] || continue
    cp "$f" "$DEST/"
    echo "  staged $(basename "$f")"
  done
done

rm -rf "$TMP_CLONE"
echo "Done. Raw FHIR NDJSON staged in $DEST"
