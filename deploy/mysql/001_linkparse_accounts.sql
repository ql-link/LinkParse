CREATE DATABASE IF NOT EXISTS linkparse_dev
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

USE linkparse_dev;

CREATE TABLE IF NOT EXISTS users (
  id BIGINT NOT NULL AUTO_INCREMENT,
  email VARCHAR(255) NOT NULL,
  username VARCHAR(64) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  is_admin BOOLEAN NOT NULL DEFAULT FALSE,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_email (email),
  UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS user_sessions (
  id BIGINT NOT NULL AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  token_hash VARCHAR(64) NOT NULL,
  expires_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL,
  last_used_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_user_sessions_token_hash (token_hash),
  KEY ix_user_sessions_user_id (user_id),
  CONSTRAINT fk_user_sessions_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS api_keys (
  id BIGINT NOT NULL AUTO_INCREMENT,
  user_id BIGINT NOT NULL,
  name VARCHAR(80) NOT NULL,
  prefix VARCHAR(16) NOT NULL,
  key_hash VARCHAR(64) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  created_at DATETIME NOT NULL,
  last_used_at DATETIME NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_api_keys_key_hash (key_hash),
  KEY ix_api_keys_user_id (user_id),
  CONSTRAINT fk_api_keys_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS parse_records (
  id BIGINT NOT NULL AUTO_INCREMENT,
  user_id BIGINT NULL,
  api_key_id BIGINT NULL,
  request_id VARCHAR(128) NOT NULL,
  job_id VARCHAR(80) NULL,
  filename VARCHAR(512) NOT NULL,
  mode VARCHAR(16) NOT NULL,
  status VARCHAR(20) NOT NULL,
  engine VARCHAR(40) NOT NULL,
  detected_type VARCHAR(80) NULL,
  page_count INT NULL,
  duration_ms INT NULL,
  error_code VARCHAR(80) NULL,
  error_message TEXT NULL,
  created_at DATETIME NOT NULL,
  completed_at DATETIME NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_parse_records_request_id (request_id),
  KEY ix_parse_records_user_created (user_id, created_at),
  KEY ix_parse_records_job_id (job_id),
  CONSTRAINT fk_parse_records_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
  CONSTRAINT fk_parse_records_api_key FOREIGN KEY (api_key_id) REFERENCES api_keys (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
