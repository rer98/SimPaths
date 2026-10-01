-- (C) Copyright 2026, by Ross Richardson
-- Initial dedicated database owner: remove cluster-wide administrator privileges.
-- @author ross richardson
REVOKE ALL ON DATABASE jasmine_multirun FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
-- Local socket administration remains available via docker exec; this role has
-- no password and cannot authenticate over the password-protected TCP listener.
CREATE ROLE postgres SUPERUSER LOGIN;
ALTER ROLE simpaths_online NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
