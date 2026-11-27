#!/bin/bash
# One-off (27 Nov 2026): portal access OFF for every 2026 member except Morty Douglass, who gets a 2026 membership
# (COMP) and access ON. Stops without changing anything if it can't find exactly one Morty Douglass in Vensoft.
set -euo pipefail
REF=${1:-main}
T="python3 $HOME/bowler-portal/tools/portal_admin.py"
curl -fsSL "https://raw.githubusercontent.com/ytrom/vensoft/$REF/tools/portal_admin.py" -o "$HOME/bowler-portal/tools/portal_admin.py"
echo "== Vensoft profiles named Morty Douglass:"; $T find "mort douglass" | tee /tmp/morty-find.txt
N=$(grep -cE '^ +[0-9]+ ' /tmp/morty-find.txt || true)
if [ "$N" != "1" ]; then echo "== Found $N, not exactly 1. Nothing changed. Copy this back to Claude."; exit 1; fi
ID=$(grep -oE '^ +[0-9]+' /tmp/morty-find.txt | tr -d ' ')
$T access-year 2026 off --by Morty --except "$ID"
$T member "$ID" 2026 --by Morty --status COMP --notes "owner" >/dev/null
$T access "$ID" on --by Morty
echo "== Now:"; $T members 2026
