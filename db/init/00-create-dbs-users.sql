DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'post_user') THEN
        CREATE ROLE post_user LOGIN PASSWORD 'post_pass';
    END IF;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'interaction_user') THEN
        CREATE ROLE interaction_user LOGIN PASSWORD 'interaction_pass';
    END IF;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'notification_user') THEN
        CREATE ROLE notification_user LOGIN PASSWORD 'notification_pass';
    END IF;
END
$$;

SELECT 'CREATE DATABASE post_db OWNER post_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'post_db')\gexec

SELECT 'CREATE DATABASE interaction_db OWNER interaction_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'interaction_db')\gexec

SELECT 'CREATE DATABASE notification_db OWNER notification_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'notification_db')\gexec
