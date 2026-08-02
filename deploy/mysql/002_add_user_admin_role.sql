USE linkparse_dev;

ALTER TABLE users
  ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE AFTER status;

-- Promote only the already-established dev operator during this one-time migration.
UPDATE users
SET is_admin = TRUE
WHERE LOWER(username) = 'root';
