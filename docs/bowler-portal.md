# Bowlers Portal: login, access switch and yearly membership

Handoff for whoever builds the Sun City Bowlers Portal (web portal / MySuncity). Self-contained, no secrets.

## The short version

- A bowler logs in with their **member number** and a **password**.
- Member number = **Sun City ID + 100000**, so every member number has 6 digits: Sun City ID 26 = member **100026**.
  Sun City ID = `public.venue_bowlerprofile.id` in the Vensoft (`bowlcontrol`) database, the same number Sun City
  writes into ComScore `MASTER.MASTERNUM`. Inside the database everything is keyed by the Sun City ID (`profile_id`);
  the portal only ever shows and accepts the member number. It is **not** the ComScore Address No. and not the old
  `legacy_bowler_id`. A plain Sun City ID (e.g. `26`) typed at login finds nobody.
- The bowler gets in only when **all** of these are true:
  1. the password is right,
  2. their portal **access switch is ON** (staff can turn it off),
  3. they hold a **membership for the current season year** (calendar year, Brisbane time; `2026`, `2027`, ...)
     with status `PAID` or `COMP`.
- The portal never sees or stores a plain password beyond the login request, and never reads the tables directly.
  It calls five database functions.

## Where it lives

Database `bowlcontrol` on the Vensoft server (PostgreSQL, VM 102). Schema `suncity` (Sun City's own tables;
Fletcher's `venue_*` tables are unchanged). Created by `sql/001_bowler_portal.sql` in `ytrom/vensoft`.

| Table | What |
|---|---|
| `suncity.portal_account` | one row per bowler: `password_hash` (Django `pbkdf2_sha256`), `access_enabled`, `must_change_password`, failed tries, lock, last login |
| `suncity.season_membership` | one row per bowler per `season_year`, `status` PAID / COMP / PENDING / CANCELLED, amount, paid date, receipt ref |
| `suncity.portal_audit` | who set/reset a password, switched access, changed a membership, lockouts |

The portal's database login (today `mysuncity_app`) has **no table access**, only EXECUTE on these functions:

| Function | Returns | Use |
|---|---|---|
| `suncity.portal_login_lookup(member_number bigint)` | one row or none: `profile_id, member_number, first_name, last_name, email, password_hash, access_enabled, must_change_password, locked_until, season_year, current_member, member_years int[]` | step 1 of login |
| `suncity.portal_record_login(profile_id bigint, ok bool, from text)` | `locked_until` or NULL | step 3: report right/wrong password. 5 wrong in a row locks that number for 15 min |
| `suncity.portal_change_password(profile_id bigint, new_hash text)` | true/false | bowler picks their own password. Takes a **hash** (Django `make_password`, pbkdf2_sha256, at least 600000 iterations), never the password |
| `suncity.portal_is_member(profile_id bigint, year int default this year)` | bool | "is this bowler a 2027 member?" for any year |
| `suncity.portal_member_years(profile_id bigint)` | int[] newest first | show "Member 2026, 2025" |
| `suncity.portal_current_season()` | int | this season year (Brisbane time) |
| `suncity.portal_member_number(profile_id)` / `suncity.portal_profile_id(member_number)` | bigint | convert between the two (+/- 100000) |

## Login steps (what the portal must do)

1. Read member number (6 digits) and password. Rate limit by IP as well.
2. `SELECT * FROM suncity.portal_login_lookup(%s)`.
3. If no row, or `password_hash` is NULL: run a dummy `check_password` anyway (same time cost) and show the
   **same** message as a wrong password: "Bowler number or password is wrong." Don't reveal which part.
4. If `locked_until` is set: "Too many tries, try again after <time>." Don't check the password.
5. `ok = check_password(password, password_hash)` (Django) then `SELECT suncity.portal_record_login(profile_id, ok, client_ip)`.
   Wrong: the same generic message.
6. Password right, then in this order:
   - `access_enabled` false: "Your portal access is turned off. Please see the front counter."
   - `current_member` false: "You don't have a {season_year} membership yet." (offer how to join/renew)
   - `must_change_password` true: let them in only to a "choose your own password" page; save with
     `portal_change_password(profile_id, make_password(new))`.
   - otherwise: signed in. Keep `profile_id` in the session.
7. Re-check access + membership on each new session (or at least daily) with `portal_login_lookup`, so a switch
   turned off or a new year starting (1 Jan) takes effect without waiting for logout.

### Django example

```python
from django.contrib.auth.hashers import check_password, make_password
from django.db import connections

DUMMY = make_password("not-a-real-password")

def portal_login(number: str, password: str, ip: str):
    if not number.isdigit():
        check_password(password, DUMMY); return ("BAD", None)
    with connections["bowlcontrol"].cursor() as c:
        c.execute("SELECT * FROM suncity.portal_login_lookup(%s)", [int(number)])
        row = c.fetchone()
        if not row or not row[4]:
            check_password(password, DUMMY); return ("BAD", None)
        (pid, member_no, first, last, email, pw_hash, access_on, must_change,
         locked_until, season, member, years) = row
        if locked_until:
            return ("LOCKED", locked_until)
        ok = check_password(password, pw_hash)
        c.execute("SELECT suncity.portal_record_login(%s, %s, %s)", [pid, ok, ip[:100]])
        if not ok:          return ("BAD", None)
        if not access_on:   return ("ACCESS_OFF", None)
        if not member:      return ("NOT_A_MEMBER", season)
        if must_change:     return ("MUST_CHANGE_PASSWORD", pid)
        return ("OK", pid)
```

The `bowlcontrol` connection for `mysuncity_app` should use `autocommit` (Django default) so
`portal_record_login` and `portal_change_password` are saved.

## The portal must not

- store, log, email or show a password, or put one in a URL. Only the hash goes to Vensoft.
- read or write the `suncity.portal_*` / `season_membership` tables directly, or ask for table grants.
- let a bowler set their own access switch or membership. Those are staff only.
- write to ComScore, or to any `venue_*` Vensoft table.
- say whether a member number exists ("wrong number" vs "wrong password").
- treat `PENDING` or `CANCELLED` as a member.
- accept a bowler-chosen password under 8 characters (staff can set shorter test passwords with the staff tool).

## Staff side (today)

Staff set/reset passwords, switch access and record memberships with `tools/portal_admin.py` on the Mission
Control server (.253). A Mission Control / Control Menu screen for this is the planned next step.

- Commands take the member number (100026) or the Sun City ID (26).
- `set-password <member>` makes a random temporary password (or `--choose` to type one in hidden) and prints a temporary one once, setting `must_change_password`.
  Staff can never view an existing password.
- `access <id> on|off`, `unlock <id>`, `member <id> <year> [--status --amount --paid-date --ref]`,
  `unmember <id> <year>`, `members <year>`, `show <id>`, `check <id> [--year]`.
- New accounts default to access ON, so in practice a bowler is in once they have a password and a paid membership
  for this year; the switch is for blocking someone.

## Not decided yet

- Self-service "forgot password" (email/SMS code to the email on the Vensoft profile). Until then staff reset it.
- Online membership payment. Until then staff record the payment with `member`.
- Fletcher's Vensoft already has `venue_membershipdefinition` / `venue_bowlermembership` (date ranges, not season
  years). These new tables don't use them; if Sun City later sells memberships through Vensoft, `portal_is_member`
  is the one place to change.
