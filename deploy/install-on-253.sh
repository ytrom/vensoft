#!/bin/bash
# Installs the Bowlers Portal tables + functions into Vensoft's bowlcontrol database (schema suncity) and puts the
# staff tool in ~/bowler-portal on the Mission Control server (.253). Safe to run again. Does not touch ComScore,
# Fletcher's venue_* tables, or Mission Control itself.
set -euo pipefail
BRANCH=${1:-main}
RAW=https://raw.githubusercontent.com/ytrom/vensoft/$BRANCH
DIR=$HOME/bowler-portal
mkdir -p "$DIR/sql" "$DIR/tools"
curl -fsSL "$RAW/sql/001_bowler_portal.sql" -o "$DIR/sql/001_bowler_portal.sql"
curl -fsSL "$RAW/tools/portal_admin.py"     -o "$DIR/tools/portal_admin.py"
chmod +x "$DIR/tools/portal_admin.py"
echo "== Files from branch $BRANCH:"; md5sum "$DIR/sql/001_bowler_portal.sql" "$DIR/tools/portal_admin.py"
python3 "$DIR/tools/portal_admin.py" install
python3 "$DIR/tools/portal_admin.py" grant-portal mysuncity_app || echo "== (mysuncity_app grant skipped, see message above)"
YEAR=$(date +%Y)
python3 "$DIR/tools/portal_admin.py" members "$YEAR" | tail -1
echo "== Done. Staff tool: python3 ~/bowler-portal/tools/portal_admin.py --help"
