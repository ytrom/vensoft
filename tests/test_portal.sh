#!/bin/bash
# Runs the Bowlers Portal SQL + staff tool against a throwaway local PostgreSQL 16 and checks the portal's view of it.
# Needs: PostgreSQL 16 binaries, python3 with psycopg2 and Django. Run as root (uses the postgres user) or as a normal user.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BIN=${PG_BIN:-/usr/lib/postgresql/16/bin}
D=$(mktemp -d); chmod 777 "$D"
RUN() { if [ "$(id -u)" = 0 ]; then su postgres -c "$*"; else bash -c "$*"; fi; }
SU=$([ "$(id -u)" = 0 ] && echo postgres || id -un)
RUN "$BIN/initdb -D $D/d -A trust >/dev/null && $BIN/pg_ctl -D $D/d -o '-k $D -p 55439 -c listen_addresses=' -l $D/log start >/dev/null"
trap 'RUN "$BIN/pg_ctl -D $D/d stop -m fast >/dev/null"; rm -rf "$D"' EXIT
P="psql -q -h $D -p 55439 -U $SU"
$P -d postgres -c "CREATE ROLE portal_test LOGIN" -c "CREATE DATABASE bowlcontrol"
$P -d bowlcontrol -c "CREATE TABLE venue_bowlerprofile(id serial PRIMARY KEY, first_name varchar(100), last_name varchar(100), email varchar(254));
  INSERT INTO venue_bowlerprofile VALUES (302,'DAMON','LEE-HONG','d@example.com'),(303,'JO','BLOGGS',NULL);"
export PORTAL_DSN="host=$D port=55439 user=$SU dbname=bowlcontrol"
T="python3 $ROOT/tools/portal_admin.py"
$T install; $T install   # twice: must be safe to re-run
PW=$($T set-password 302 --by test | grep -o '[a-z0-9]\{4\}-[a-z0-9]\{4\}-[a-z0-9]\{4\}')
YEAR=$($P -d bowlcontrol -tAc "SELECT suncity.portal_current_season()")
$T member 302 "$YEAR" --by test --amount 50 >/dev/null
$T member 302 $((YEAR+1)) --by test --status PENDING >/dev/null
$T member 303 "$YEAR" --by test >/dev/null
$T grant-portal portal_test >/dev/null
PW="$PW" YEAR="$YEAR" DSN="host=$D port=55439 user=portal_test dbname=bowlcontrol" python3 - <<'PY'
import os, psycopg2, django
from django.conf import settings; settings.configure(); django.setup()
from django.contrib.auth.hashers import check_password, make_password
c = psycopg2.connect(os.environ["DSN"]); c.autocommit = True; cur = c.cursor()
Y = int(os.environ["YEAR"]); fails = []
def ok(cond, what): print(("PASS " if cond else "FAIL ") + what); cond or fails.append(what)
try:
    cur.execute("SELECT password_hash FROM suncity.portal_account"); ok(False, "portal role can't read tables")
except psycopg2.errors.InsufficientPrivilege:
    ok(True, "portal role can't read tables")
cur.execute("SELECT * FROM suncity.portal_login_lookup(302)"); r = cur.fetchone()
ok(r[0] == 302 and r[5] and r[6] and r[9] and r[8] == Y, "lookup: access on, must change, member this year")
ok(check_password(os.environ["PW"], r[4]) and not check_password("wrong", r[4]), "Django check_password reads staff hash")
cur.execute("SELECT * FROM suncity.portal_login_lookup(999)"); ok(cur.fetchone() is None, "unknown number gives no row")
cur.execute("SELECT password_hash FROM suncity.portal_login_lookup(303)"); ok(cur.fetchone()[0] is None, "member without password has NULL hash")
cur.execute("SELECT suncity.portal_is_member(302, %s), suncity.portal_is_member(302, %s)", (Y, Y + 1))
ok(cur.fetchone() == (True, False), "PENDING next year is not a member")
for i in range(5):
    cur.execute("SELECT suncity.portal_record_login(302, false, 'test')"); last = cur.fetchone()[0]
ok(last is not None, "5 wrong tries lock the number")
try:
    cur.execute("SELECT suncity.portal_change_password(302, 'plaintext')"); ok(False, "plain password refused")
except psycopg2.Error:
    ok(True, "plain password refused")
cur.execute("SELECT suncity.portal_change_password(302, %s)", (make_password("Bowl#2027"),))
cur.execute("SELECT password_hash, must_change_password FROM suncity.portal_login_lookup(302)"); h, m = cur.fetchone()
ok(check_password("Bowl#2027", h) and not m, "bowler's own password saved, must_change cleared")
raise SystemExit(1 if fails else 0)
PY
$T unlock 302 --by test >/dev/null; $T access 302 off --by test >/dev/null
$T check 302 | grep -q ACCESS_OFF && echo "PASS access switch off blocks" || { echo "FAIL access switch"; exit 1; }
echo "All portal tests passed."
