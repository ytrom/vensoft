-- Bowlers Portal: password, access switch and season (yearly) memberships for Vensoft bowlers.
-- Database: bowlcontrol (VM 102, 192.168.0.252). Schema: suncity (Sun City's own tables, next to
-- suncity.comscore_link and suncity.bowler_details). Fletcher's venue_* tables are NOT changed.
--
-- Member number (what bowlers type to log in) = Sun City ID + 100000, e.g. Sun City ID 26 = member 100026.
-- Sun City ID = public.venue_bowlerprofile.id (the number written into ComScore MASTER.MASTERNUM); tables keep
-- that ID. Safe to run more than once. Nothing here touches ComScore.

CREATE SCHEMA IF NOT EXISTS suncity;

-- One row per bowler who has portal access set up.
CREATE TABLE IF NOT EXISTS suncity.portal_account (
    profile_id           bigint   PRIMARY KEY REFERENCES public.venue_bowlerprofile(id) ON DELETE CASCADE,
    -- Django-format hash only, e.g. pbkdf2_sha256$1000000$<salt>$<hash>. Never a plain password.
    password_hash        text,
    -- Staff switch. OFF blocks the portal even for a paid member.
    access_enabled       boolean     NOT NULL DEFAULT true,
    -- Set when staff set or reset a password, so the portal asks the bowler to choose their own.
    must_change_password boolean     NOT NULL DEFAULT true,
    password_set_at      timestamptz,
    password_set_by      text        NOT NULL DEFAULT '',
    failed_attempts      integer     NOT NULL DEFAULT 0,
    locked_until         timestamptz,
    last_login_at        timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT portal_hash_format CHECK (password_hash IS NULL OR password_hash ~ '^pbkdf2_sha256\$[0-9]+\$[^$]+\$[^$]+$')
);

-- One row per bowler per season year (2026, 2027, ...). A bowler can hold many years.
CREATE TABLE IF NOT EXISTS suncity.season_membership (
    id          serial PRIMARY KEY,
    profile_id  bigint       NOT NULL REFERENCES public.venue_bowlerprofile(id) ON DELETE CASCADE,
    season_year smallint    NOT NULL CHECK (season_year BETWEEN 2000 AND 2100),
    -- PAID and COMP (free / life member) count as a member. PENDING (not paid yet) and CANCELLED don't.
    status      text        NOT NULL DEFAULT 'PAID' CHECK (status IN ('PAID', 'COMP', 'PENDING', 'CANCELLED')),
    amount      numeric(8,2),
    paid_date   date,
    payment_ref text        NOT NULL DEFAULT '',
    notes       text        NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text        NOT NULL DEFAULT '',
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text        NOT NULL DEFAULT '',
    UNIQUE (profile_id, season_year)
);
CREATE INDEX IF NOT EXISTS season_membership_year ON suncity.season_membership (season_year, status);

-- Who changed what (password set/reset, access on/off, membership changes, lockouts). Never holds a password.
CREATE TABLE IF NOT EXISTS suncity.portal_audit (
    id         bigserial PRIMARY KEY,
    profile_id bigint       NOT NULL,
    action     text        NOT NULL,
    detail     text        NOT NULL DEFAULT '',
    done_by    text        NOT NULL DEFAULT '',
    done_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS portal_audit_profile ON suncity.portal_audit (profile_id, done_at DESC);

-- ---------------------------------------------------------------------------------------------
-- Functions the portal calls. The portal's database login gets EXECUTE on these only, no table
-- access, so it can check one bowler at a time but can't list everyone's password hashes.
-- ---------------------------------------------------------------------------------------------

-- The season year right now, in Brisbane time (1 Jan to 31 Dec).
CREATE OR REPLACE FUNCTION suncity.portal_current_season() RETURNS integer
LANGUAGE sql STABLE AS $$
    SELECT extract(year FROM (now() AT TIME ZONE 'Australia/Brisbane'))::integer
$$;

-- Member number <-> Sun City ID. Member numbers start at 100000.
CREATE OR REPLACE FUNCTION suncity.portal_member_number(p_profile_id bigint) RETURNS bigint
LANGUAGE sql IMMUTABLE AS $$ SELECT p_profile_id + 100000 $$;
CREATE OR REPLACE FUNCTION suncity.portal_profile_id(p_member_number bigint) RETURNS bigint
LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN p_member_number > 100000 THEN p_member_number - 100000 END $$;

-- Is this bowler a member for that year? Year left out = this year.
CREATE OR REPLACE FUNCTION suncity.portal_is_member(p_profile_id bigint, p_year integer DEFAULT NULL)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, suncity AS $$
    SELECT EXISTS (
        SELECT 1 FROM suncity.season_membership m
        WHERE m.profile_id = p_profile_id
          AND m.season_year = coalesce(p_year, suncity.portal_current_season())
          AND m.status IN ('PAID', 'COMP'))
$$;

-- Every year this bowler has been a member, newest first.
CREATE OR REPLACE FUNCTION suncity.portal_member_years(p_profile_id bigint)
RETURNS integer[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, suncity AS $$
    SELECT coalesce(array_agg(m.season_year::integer ORDER BY m.season_year DESC), '{}')
    FROM suncity.season_membership m
    WHERE m.profile_id = p_profile_id AND m.status IN ('PAID', 'COMP')
$$;

-- Step 1 of a login: everything the portal needs about ONE member number (e.g. 100026).
-- No row back = no such member number. password_hash NULL = no password set yet.
-- (Dropped first because its columns changed on 27 Nov 2026; install re-grants it.)
DROP FUNCTION IF EXISTS suncity.portal_login_lookup(bigint);
CREATE FUNCTION suncity.portal_login_lookup(p_member_number bigint)
RETURNS TABLE (
    profile_id           bigint,
    member_number        bigint,
    first_name           text,
    last_name            text,
    email                text,
    password_hash        text,
    access_enabled       boolean,
    must_change_password boolean,
    locked_until         timestamptz,
    season_year          integer,
    current_member       boolean,
    member_years         integer[]
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, suncity AS $$
    SELECT p.id, suncity.portal_member_number(p.id), p.first_name::text, p.last_name::text, p.email::text,
           a.password_hash,
           coalesce(a.access_enabled, false),
           coalesce(a.must_change_password, true),
           CASE WHEN a.locked_until > now() THEN a.locked_until END,
           suncity.portal_current_season(),
           suncity.portal_is_member(p.id),
           suncity.portal_member_years(p.id)
    FROM public.venue_bowlerprofile p
    LEFT JOIN suncity.portal_account a ON a.profile_id = p.id
    WHERE p.id = suncity.portal_profile_id(p_member_number)
$$;

-- Step 3 of a login: tell Vensoft whether the password was right.
-- 5 wrong in a row locks that bowler number for 15 minutes. A right one clears the count.
CREATE OR REPLACE FUNCTION suncity.portal_record_login(p_profile_id bigint, p_ok boolean, p_from text DEFAULT '')
RETURNS timestamptz  -- locked_until, NULL when not locked
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, suncity AS $$
DECLARE
    v_locked timestamptz;
BEGIN
    IF p_ok THEN
        UPDATE suncity.portal_account
           SET failed_attempts = 0, locked_until = NULL, last_login_at = now()
         WHERE profile_id = p_profile_id;
        RETURN NULL;
    END IF;
    UPDATE suncity.portal_account
       SET failed_attempts = failed_attempts + 1,
           locked_until = CASE WHEN failed_attempts + 1 >= 5 THEN now() + interval '15 minutes' ELSE locked_until END
     WHERE profile_id = p_profile_id
     RETURNING CASE WHEN locked_until > now() THEN locked_until END INTO v_locked;
    IF v_locked IS NOT NULL THEN
        UPDATE suncity.portal_account SET failed_attempts = 0 WHERE profile_id = p_profile_id;
        INSERT INTO suncity.portal_audit (profile_id, action, detail, done_by)
        VALUES (p_profile_id, 'LOCKED', 'five wrong passwords', left(coalesce(p_from, ''), 100));
    END IF;
    RETURN v_locked;
END
$$;

-- The bowler chooses their own password (after the portal has checked the old one).
-- Takes a HASH made by the portal (Django make_password), never the password itself.
CREATE OR REPLACE FUNCTION suncity.portal_change_password(p_profile_id bigint, p_new_hash text)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, suncity AS $$
BEGIN
    IF p_new_hash IS NULL OR p_new_hash !~ '^pbkdf2_sha256\$[0-9]+\$[^$]+\$[^$]+$'
       OR split_part(p_new_hash, '$', 2)::integer < 600000 THEN
        RAISE EXCEPTION 'portal_change_password needs a Django pbkdf2_sha256 hash with at least 600000 iterations';
    END IF;
    UPDATE suncity.portal_account
       SET password_hash = p_new_hash, must_change_password = false,
           password_set_at = now(), password_set_by = 'bowler', updated_at = now()
     WHERE profile_id = p_profile_id;
    IF NOT FOUND THEN RETURN false; END IF;
    INSERT INTO suncity.portal_audit (profile_id, action, done_by) VALUES (p_profile_id, 'PASSWORD_CHANGED', 'bowler');
    RETURN true;
END
$$;

-- Functions are callable by everyone by default in PostgreSQL; take that away, then grant per role
-- with: python3 tools/portal_admin.py grant-portal <role>
REVOKE ALL ON FUNCTION suncity.portal_is_member(bigint, integer)          FROM PUBLIC;
REVOKE ALL ON FUNCTION suncity.portal_member_years(bigint)                FROM PUBLIC;
REVOKE ALL ON FUNCTION suncity.portal_login_lookup(bigint)                FROM PUBLIC;
REVOKE ALL ON FUNCTION suncity.portal_record_login(bigint, boolean, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION suncity.portal_change_password(bigint, text)       FROM PUBLIC;
