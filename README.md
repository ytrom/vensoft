# vensoft (Sun City add-ons for Vensoft / BowlControl)

Sun City's own additions next to Fletcher's BowlControl (Vensoft) database. Nothing here changes Fletcher's
`venue_*` tables or writes to ComScore. No passwords or secrets belong in this repo (it is public).

- `sql/001_bowler_portal.sql`: Bowlers Portal password, access switch and season memberships (schema `suncity`).
- `tools/portal_admin.py`: staff tool for the above, run on the Mission Control server (.253).
- `docs/bowler-portal.md`: how the web portal checks login + current-year membership.
- `tests/test_portal.sh`: runs everything against a throwaway local PostgreSQL.

Install / update on .253 (from PowerShell on Morty's PC):
`ssh -i $HOME\.ssh\mc253 suncityas@192.168.0.253 'curl -fsSL https://raw.githubusercontent.com/ytrom/vensoft/main/deploy/install-on-253.sh | bash -s main'`
