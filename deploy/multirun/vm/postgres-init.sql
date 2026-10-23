-- (C) Copyright 2026, by Ross Richardson
-- Separate the application role from PostgreSQL's permanent bootstrap superuser.
-- @author ross richardson
BEGIN;
DO $simpaths$
DECLARE
    verifier text;
BEGIN
    IF current_user <> 'postgres' THEN
        RAISE EXCEPTION 'Initialize with POSTGRES_USER=postgres';
    END IF;
    SELECT rolpassword INTO STRICT verifier FROM pg_authid WHERE rolname = current_user;
    IF verifier IS NULL OR verifier NOT LIKE 'SCRAM-SHA-256$%' THEN
        RAISE EXCEPTION 'A SCRAM bootstrap credential is required';
    END IF;
    -- Keep the generated password inside PostgreSQL. Never put it in SQL files,
    -- command arguments or logs. The passwordless operator uses local sockets.
    EXECUTE format('CREATE ROLE simpaths_online LOGIN NOSUPERUSER NOCREATEDB '
        'NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L', verifier);
END;
$simpaths$;
ALTER DATABASE jasmine_multirun OWNER TO simpaths_online;
REVOKE ALL ON DATABASE jasmine_multirun FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
-- PostgreSQL 16+ forbids removing SUPERUSER from the bootstrap role. Keep it
-- for local operator access and remove its password, so SCRAM TCP login fails.
ALTER ROLE postgres PASSWORD NULL;
COMMIT;
