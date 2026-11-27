#!/usr/bin/env python3
"""Staff tool for the Bowlers Portal: passwords, the access switch and season memberships.

Runs on the Mission Control server (.253) and reads the database login from Mission Control's
lib/db.ts, so no password is kept in this repo. Writes only to the bowlcontrol "suncity" schema.
Never writes to ComScore.

  python3 portal_admin.py install                         create/upgrade the tables and functions
  python3 portal_admin.py sync-usernames                  give new Vensoft bowlers their login username
  python3 portal_admin.py show 302                        one bowler: access, lock, member years
  python3 portal_admin.py set-password 302 --by Morty     make a temporary password (printed once)
  python3 portal_admin.py access 302 off --by Morty       portal switch on/off
  python3 portal_admin.py unlock 302 --by Morty           clear a 5-wrong-tries lock
  python3 portal_admin.py member 302 2027 --by Morty [--status PAID|COMP|PENDING|CANCELLED] [--amount 50] [--paid-date 2027-01-10] [--ref RCPT123]
  python3 portal_admin.py unmember 302 2027 --by Morty    remove that year
  python3 portal_admin.py members 2027                    list that year's members
  python3 portal_admin.py check 302 [--year 2027]         what the portal would decide (no password needed)
  python3 portal_admin.py find "morty douglass"            look up a member number by name
  python3 portal_admin.py access-year 2026 off --by Morty --except 12   switch a whole year's members
  python3 portal_admin.py grant-portal mysuncity_app      let the portal's login call the check functions

Bowlers are given by MEMBER NUMBER (Sun City ID + 100000, e.g. 100026) or by Sun City ID (26); both work.
"""
import argparse
import base64
import hashlib
import os
import re
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SQL_FILE = os.path.join(HERE, "..", "sql", "001_bowler_portal.sql")
MC_DB_TS = os.environ.get("MC_DB_TS", "/home/suncityas/mission-control/lib/db.ts")

# Same format and strength as Django 5.2's default hasher, so Django's check_password() reads it.
ITERATIONS = 1_000_000
TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


MEMBER_OFFSET = 100000


def bowler(value):
    """Member number (100026) or Sun City ID (26) -> Sun City ID."""
    n = int(value)
    return n - MEMBER_OFFSET if n > MEMBER_OFFSET else n


def member_no(pid):
    return pid + MEMBER_OFFSET


def make_password(raw, salt=None, iterations=ITERATIONS):
    salt = salt or "".join(secrets.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(22))
    dk = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(dk).decode().strip()}"


def check_password(raw, encoded):
    try:
        algo, iters, salt, _ = encoded.split("$", 3)
    except (AttributeError, ValueError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    return secrets.compare_digest(make_password(raw, salt, int(iters)), encoded)


def temp_password():
    # e.g. "k7mq-2xhp-9tad": 12 random characters, easy to read out over the phone.
    raw = "".join(secrets.choice(TEMP_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def connect():
    import psycopg2
    if os.environ.get("PORTAL_DSN"):
        return psycopg2.connect(os.environ["PORTAL_DSN"])
    src = open(MC_DB_TS, encoding="utf-8").read()
    get = lambda k: (re.search(k + r':\s*"([^"]+)"', src) or [None, None])[1]
    return psycopg2.connect(host=get("host"), port=5432, user=get("user"), password=get("password"), dbname="bowlcontrol")


def audit(cur, pid, action, detail, by):
    cur.execute("INSERT INTO suncity.portal_audit (profile_id, action, detail, done_by) VALUES (%s,%s,%s,%s)",
                (pid, action, detail, by))


def username_of(cur, pid):
    cur.execute("SELECT login_username FROM suncity.portal_account WHERE profile_id = %s", (pid,))
    r = cur.fetchone()
    return r[0] if r else None


def need_profile(cur, pid):
    cur.execute("SELECT first_name, last_name FROM public.venue_bowlerprofile WHERE id = %s", (pid,))
    row = cur.fetchone()
    if not row:
        sys.exit(f"No Vensoft bowler with member number {member_no(pid)} (Sun City ID {pid}).")
    return f"{row[0] or ''} {row[1] or ''}".strip()


def ensure_account(cur, pid):
    cur.execute("INSERT INTO suncity.portal_account (profile_id, access_enabled) VALUES (%s, false) ON CONFLICT DO NOTHING", (pid,))


def cmd_install(cur, a):
    cur.execute(open(SQL_FILE, encoding="utf-8").read())
    print("Installed: suncity.portal_account, suncity.season_membership, suncity.portal_audit + portal_* functions.")
    cur.execute("SELECT count(*), count(*) FILTER (WHERE access_enabled) FROM suncity.portal_account")
    n, on = cur.fetchone()
    print(f"Login usernames saved for {n} Vensoft bowlers; portal access ON for {on}.")


def cmd_sync(cur, a):
    """Give every Vensoft bowler without one a saved login username (member number). New ones start access OFF."""
    cur.execute("""INSERT INTO suncity.portal_account (profile_id, access_enabled)
                   SELECT p.id, false FROM public.venue_bowlerprofile p ON CONFLICT (profile_id) DO NOTHING""")
    added = cur.rowcount
    cur.execute("SELECT count(*) FROM suncity.portal_account")
    print(f"Login usernames: {cur.fetchone()[0]} bowlers have one ({added} added just now).")


def cmd_show(cur, a):
    name = need_profile(cur, a.id)
    ensure_account(cur, a.id)
    cur.execute("SELECT * FROM suncity.portal_login_lookup(%s)", (username_of(cur, a.id),))
    r = cur.fetchone()
    cur.execute("SELECT season_year, status, amount, paid_date, payment_ref FROM suncity.season_membership "
                "WHERE profile_id = %s ORDER BY season_year DESC", (a.id,))
    years = cur.fetchall()
    cur.execute("SELECT last_login_at, password_set_at, password_set_by FROM suncity.portal_account WHERE profile_id = %s", (a.id,))
    acc = cur.fetchone()
    print(f"Login username {r[1]} (Sun City ID {a.id}): {name}  email {r[4] or '-'}")
    print(f"  Password:  {'set' if r[5] else 'NOT SET'}" + (f" ({acc[1]:%d %b %Y} by {acc[2]})" if acc and acc[1] else "")
          + ("  - must choose a new one at next login" if r[5] and r[7] else ""))
    print(f"  Access:    {'ON' if r[6] else 'OFF'}" + (f"   LOCKED until {r[8]}" if r[8] else ""))
    print(f"  Member {r[9]}: {'YES' if r[10] else 'NO'}")
    for y in years:
        print(f"    {y[0]}  {y[1]:<9} {'$' + str(y[2]) if y[2] is not None else '':<9} {y[3] or ''} {y[4] or ''}")
    if acc and acc[0]:
        print(f"  Last portal login: {acc[0]}")


def cmd_set_password(cur, a):
    name = need_profile(cur, a.id)
    if a.choose:
        # Typed in (not shown, not stored). Only for a bowler choosing their own, e.g. staff setting their own test login.
        import getpass
        raw = getpass.getpass(f"New portal password for {name} (member {member_no(a.id)}, Sun City ID {a.id}): ")
        if raw != getpass.getpass("Type it again: "):
            sys.exit("The two didn't match. Nothing changed.")
        if len(raw) < 4:
            sys.exit("At least 4 characters. Nothing changed.")
        if len(raw) < 8:
            print("Warning: under 8 characters is easy to guess. Saving it anyway.")
    else:
        raw = temp_password()
    ensure_account(cur, a.id)
    cur.execute("UPDATE suncity.portal_account SET password_hash=%s, must_change_password=%s, password_set_at=now(), "
                "password_set_by=%s, failed_attempts=0, locked_until=NULL, updated_at=now() WHERE profile_id=%s",
                (make_password(raw), not a.choose, a.by, a.id))
    audit(cur, a.id, "PASSWORD_SET", "chosen password typed in" if a.choose else "temporary password by staff", a.by)
    if a.choose:
        print(f"Password saved for {name} (member {member_no(a.id)}, Sun City ID {a.id}). It is stored only as a hash.")
        return
    print(f"Temporary password for {name} (member {member_no(a.id)}, Sun City ID {a.id}):  {raw}")
    print("Give it to the bowler now. It is not stored anywhere and can't be shown again.")
    print("The portal will ask them to choose their own password the first time they log in.")


def cmd_access(cur, a):
    name = need_profile(cur, a.id)
    on = a.state == "on"
    ensure_account(cur, a.id)
    cur.execute("UPDATE suncity.portal_account SET access_enabled=%s, updated_at=now() WHERE profile_id=%s", (on, a.id))
    audit(cur, a.id, "ACCESS_ON" if on else "ACCESS_OFF", a.reason or "", a.by)
    print(f"Portal access for {name} is now {'ON' if on else 'OFF'}.")


def cmd_unlock(cur, a):
    name = need_profile(cur, a.id)
    cur.execute("UPDATE suncity.portal_account SET failed_attempts=0, locked_until=NULL WHERE profile_id=%s", (a.id,))
    audit(cur, a.id, "UNLOCKED", "", a.by)
    print(f"{name} unlocked.")


def cmd_member(cur, a):
    name = need_profile(cur, a.id)
    cur.execute("""
        INSERT INTO suncity.season_membership (profile_id, season_year, status, amount, paid_date, payment_ref, notes, created_by, updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (profile_id, season_year) DO UPDATE SET status=EXCLUDED.status, amount=EXCLUDED.amount,
            paid_date=EXCLUDED.paid_date, payment_ref=EXCLUDED.payment_ref, notes=EXCLUDED.notes,
            updated_at=now(), updated_by=EXCLUDED.updated_by""",
                (a.id, a.year, a.status, a.amount, a.paid_date, a.ref or "", a.notes or "", a.by, a.by))
    audit(cur, a.id, "MEMBER_SET", f"{a.year} {a.status}", a.by)
    print(f"{name}: {a.year} membership {a.status}.")


def cmd_unmember(cur, a):
    name = need_profile(cur, a.id)
    cur.execute("DELETE FROM suncity.season_membership WHERE profile_id=%s AND season_year=%s", (a.id, a.year))
    audit(cur, a.id, "MEMBER_REMOVED", str(a.year), a.by)
    print(f"{name}: {a.year} membership {'removed' if cur.rowcount else 'was not there'}.")


def cmd_members(cur, a):
    cur.execute("""SELECT coalesce(pa.login_username, (p.id + 100000)::text), p.first_name, p.last_name, m.status, coalesce(pa.access_enabled, false), pa.password_hash IS NOT NULL
                   FROM suncity.season_membership m JOIN public.venue_bowlerprofile p ON p.id = m.profile_id
                   LEFT JOIN suncity.portal_account pa ON pa.profile_id = p.id
                   WHERE m.season_year = %s ORDER BY p.last_name, p.first_name""", (a.year,))
    rows = cur.fetchall()
    for r in rows:
        print(f"{r[0]:>7}  {(r[1] or '') + ' ' + (r[2] or ''):<32} {r[3]:<9} access {'ON ' if r[4] else 'OFF'}  password {'set' if r[5] else 'NOT SET'}")
    print(f"{len(rows)} rows for {a.year}.")


def cmd_find(cur, a):
    cur.execute("""SELECT coalesce(pa.login_username, (p.id + 100000)::text), p.first_name, p.last_name, p.email, suncity.portal_is_member(p.id)
                   FROM public.venue_bowlerprofile p LEFT JOIN suncity.portal_account pa ON pa.profile_id = p.id
                   WHERE (coalesce(p.first_name,'') || ' ' || coalesce(p.last_name,'')) ILIKE %s ORDER BY p.id""",
                ("%" + "%".join(a.name.split()) + "%",))
    rows = cur.fetchall()
    for r in rows:
        print(f"{r[0]:>7}  {(r[1] or '') + ' ' + (r[2] or ''):<32} {r[3] or '-':<34} member this year: {'YES' if r[4] else 'no'}")
    print(f"{len(rows)} found.")


def cmd_access_year(cur, a):
    """Switch access on/off for every member of one year, leaving out --except ids."""
    on = a.state == "on"
    keep = a.except_ids or []
    cur.execute("""SELECT m.profile_id FROM suncity.season_membership m
                   WHERE m.season_year = %s AND m.status IN ('PAID','COMP') AND NOT (m.profile_id = ANY(%s))""",
                (a.year, keep))
    ids = [r[0] for r in cur.fetchall()]
    for pid in ids:
        ensure_account(cur, pid)
        cur.execute("UPDATE suncity.portal_account SET access_enabled=%s, updated_at=now() WHERE profile_id=%s", (on, pid))
        audit(cur, pid, "ACCESS_ON" if on else "ACCESS_OFF", f"all {a.year} members", a.by)
    print(f"Access {'ON' if on else 'OFF'} for {len(ids)} {a.year} members" + (f" (left out: {', '.join(str(member_no(k)) for k in keep)})" if keep else "") + ".")


def decide(row):
    """What the portal should do for a bowler AFTER the password was checked. Mirrors docs/bowler-portal.md."""
    if not row[6]:
        return "ACCESS_OFF"
    if not row[10]:
        return "NOT_A_MEMBER"
    if row[7]:
        return "OK_MUST_CHANGE_PASSWORD"
    return "OK"


def cmd_check(cur, a):
    need_profile(cur, a.id)
    ensure_account(cur, a.id)
    cur.execute("SELECT * FROM suncity.portal_login_lookup(%s)", (username_of(cur, a.id),))
    r = cur.fetchone()
    if a.year:
        cur.execute("SELECT suncity.portal_is_member(%s, %s)", (a.id, a.year))
        print(f"Member {a.year}: {'YES' if cur.fetchone()[0] else 'NO'}")
    if not r[5]:
        print("Portal would say: NO_PASSWORD (staff need to set one)")
    elif r[8]:
        print(f"Portal would say: LOCKED until {r[8]}")
    else:
        print(f"Portal would say (with the right password): {decide(r)}")


def cmd_grant(cur, a):
    role = a.role
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        sys.exit("Role names are lower case letters, digits and _.")
    cur.execute(f"""
        GRANT USAGE ON SCHEMA suncity TO {role};
        GRANT EXECUTE ON FUNCTION suncity.portal_current_season() TO {role};
        GRANT EXECUTE ON FUNCTION suncity.portal_member_number(bigint) TO {role};
        GRANT EXECUTE ON FUNCTION suncity.portal_profile_id(bigint) TO {role};
        GRANT EXECUTE ON FUNCTION suncity.portal_is_member(bigint, integer) TO {role};
        GRANT EXECUTE ON FUNCTION suncity.portal_member_years(bigint) TO {role};
        GRANT EXECUTE ON FUNCTION suncity.portal_login_lookup(text) TO {role};
        GRANT EXECUTE ON FUNCTION suncity.portal_record_login(bigint, boolean, text) TO {role};
        GRANT EXECUTE ON FUNCTION suncity.portal_change_password(bigint, text) TO {role};""")
    print(f"{role} can now call the portal_* functions (no table access).")


def main(argv=None):
    p = argparse.ArgumentParser(description="Bowlers Portal staff tool")
    s = p.add_subparsers(dest="cmd", required=True)
    s.add_parser("install")
    s.add_parser("sync-usernames")
    x = s.add_parser("show"); x.add_argument("id", type=bowler)
    x = s.add_parser("set-password"); x.add_argument("id", type=bowler); x.add_argument("--by", required=True)
    x.add_argument("--choose", action="store_true", help="type the password in instead of making a temporary one")
    x = s.add_parser("access"); x.add_argument("id", type=bowler); x.add_argument("state", choices=["on", "off"])
    x.add_argument("--by", required=True); x.add_argument("--reason")
    x = s.add_parser("unlock"); x.add_argument("id", type=bowler); x.add_argument("--by", required=True)
    x = s.add_parser("member"); x.add_argument("id", type=bowler); x.add_argument("year", type=int)
    x.add_argument("--by", required=True); x.add_argument("--status", default="PAID", choices=["PAID", "COMP", "PENDING", "CANCELLED"])
    x.add_argument("--amount", type=float); x.add_argument("--paid-date"); x.add_argument("--ref"); x.add_argument("--notes")
    x = s.add_parser("unmember"); x.add_argument("id", type=bowler); x.add_argument("year", type=int); x.add_argument("--by", required=True)
    x = s.add_parser("members"); x.add_argument("year", type=int)
    x = s.add_parser("check"); x.add_argument("id", type=bowler); x.add_argument("--year", type=int)
    x = s.add_parser("find"); x.add_argument("name")
    x = s.add_parser("access-year"); x.add_argument("year", type=int); x.add_argument("state", choices=["on", "off"])
    x.add_argument("--by", required=True); x.add_argument("--except", dest="except_ids", type=bowler, nargs="*")
    x = s.add_parser("grant-portal"); x.add_argument("role")
    a = p.parse_args(argv)
    fn = {"install": cmd_install, "sync-usernames": cmd_sync, "show": cmd_show, "set-password": cmd_set_password, "access": cmd_access,
          "unlock": cmd_unlock, "member": cmd_member, "unmember": cmd_unmember, "members": cmd_members,
          "check": cmd_check, "find": cmd_find, "access-year": cmd_access_year, "grant-portal": cmd_grant}[a.cmd]
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            fn(cur, a)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
