USE linkparse_dev;

ALTER TABLE parse_records
  DROP INDEX uq_parse_records_request_id,
  ADD INDEX ix_parse_records_request_id (request_id);
