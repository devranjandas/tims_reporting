-- Database structure made by TIMS-Report before migrations existed (create_all, 2026-11-06).
-- Used by tests/test_migrations.py to check that such databases are adopted correctly.
CREATE TABLE user (
	id INTEGER NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	full_name VARCHAR(120) NOT NULL, 
	designation VARCHAR(120) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	active BOOLEAN NOT NULL, 
	must_change_password BOOLEAN NOT NULL, 
	failed_logins INTEGER NOT NULL, 
	locked_until DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);
CREATE TABLE report_counter (
	year INTEGER NOT NULL, 
	last_seq INTEGER NOT NULL, 
	PRIMARY KEY (year)
);
CREATE TABLE audit_log (
	id INTEGER NOT NULL, 
	ts DATETIME NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	action VARCHAR(60) NOT NULL, 
	object_type VARCHAR(40) NOT NULL, 
	object_id VARCHAR(120) NOT NULL, 
	details JSON, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_audit_log_object_id ON audit_log (object_id);
CREATE INDEX ix_audit_log_ts ON audit_log (ts);
CREATE TABLE setting (
	"key" VARCHAR(60) NOT NULL, 
	value JSON NOT NULL, 
	PRIMARY KEY ("key")
);
CREATE TABLE sample (
	id INTEGER NOT NULL, 
	sample_id VARCHAR(80) NOT NULL, 
	element VARCHAR(4) NOT NULL, 
	sample_type VARCHAR(16) NOT NULL, 
	analysis_date DATE, 
	instrument VARCHAR(80) NOT NULL, 
	analyst_name VARCHAR(120) NOT NULL, 
	n_measurements INTEGER NOT NULL, 
	run_id VARCHAR(80) NOT NULL, 
	batch_ref VARCHAR(80) NOT NULL, 
	enriched_isotope INTEGER, 
	reference_sample VARCHAR(80) NOT NULL, 
	remarks TEXT NOT NULL, 
	rsd_unit VARCHAR(10) NOT NULL, 
	entries JSON NOT NULL, 
	results JSON, 
	findings JSON NOT NULL, 
	reference_info JSON, 
	status VARCHAR(12) NOT NULL, 
	version INTEGER NOT NULL, 
	lock_version INTEGER NOT NULL, 
	element_def_version VARCHAR(40) NOT NULL, 
	created_by_id INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	submitted_at DATETIME, 
	reviewed_by_id INTEGER, 
	reviewed_at DATETIME, 
	review_comment TEXT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_sample_element_id UNIQUE (element, sample_id), 
	FOREIGN KEY(created_by_id) REFERENCES user (id), 
	FOREIGN KEY(reviewed_by_id) REFERENCES user (id)
);
CREATE TABLE baseline (
	id INTEGER NOT NULL, 
	element VARCHAR(4) NOT NULL, 
	version INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	members JSON NOT NULL, 
	stats JSON NOT NULL, 
	note TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	created_by_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_baseline_version UNIQUE (element, version), 
	FOREIGN KEY(created_by_id) REFERENCES user (id)
);
CREATE TABLE saved_header (
	id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	division_code VARCHAR(8) NOT NULL, 
	organisation VARCHAR(200) NOT NULL, 
	division VARCHAR(200) NOT NULL, 
	section VARCHAR(200) NOT NULL, 
	address VARCHAR(300) NOT NULL, 
	form_number VARCHAR(80) NOT NULL, 
	logo BLOB, 
	logo_mime VARCHAR(32) NOT NULL, 
	created_by_id INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name), 
	FOREIGN KEY(created_by_id) REFERENCES user (id)
);
CREATE TABLE report (
	id INTEGER NOT NULL, 
	report_id VARCHAR(60) NOT NULL, 
	revision INTEGER NOT NULL, 
	draft BOOLEAN NOT NULL, 
	file_stem VARCHAR(80) NOT NULL, 
	content VARCHAR(20) NOT NULL, 
	layout VARCHAR(20) NOT NULL, 
	sample_list TEXT NOT NULL, 
	revision_reason TEXT NOT NULL, 
	created_by_id INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_report_revision UNIQUE (report_id, revision), 
	UNIQUE (file_stem), 
	FOREIGN KEY(created_by_id) REFERENCES user (id)
);
CREATE TABLE sample_version (
	id INTEGER NOT NULL, 
	sample_pk INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	snapshot JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	created_by_id INTEGER NOT NULL, 
	reason TEXT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_sample_version UNIQUE (sample_pk, version), 
	FOREIGN KEY(sample_pk) REFERENCES sample (id), 
	FOREIGN KEY(created_by_id) REFERENCES user (id)
);
