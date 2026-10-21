-- Generated from app/models.py by scripts/manage.py export.

-- Initial schema only. Run on an empty database; future changes need migrations.

PRAGMA foreign_keys=ON;

BEGIN;

CREATE TABLE exercises (
	id VARCHAR(16) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	title VARCHAR(200) NOT NULL,
	knowledge_point_ids JSON NOT NULL,
	content JSON NOT NULL,
	answer JSON,
	source_file TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_exercise_kind CHECK (kind IN ('case','question'))
);

CREATE INDEX ix_exercises_kind ON exercises (kind);

CREATE TABLE knowledge_points (
	id VARCHAR(16) NOT NULL,
	title VARCHAR(200) NOT NULL,
	chapter INTEGER NOT NULL,
	definition TEXT NOT NULL,
	example TEXT NOT NULL,
	common_mistake TEXT NOT NULL,
	source TEXT NOT NULL,
	tags JSON NOT NULL,
	difficulty VARCHAR(16) NOT NULL,
	related_features JSON NOT NULL,
	source_file TEXT NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_knowledge_points_chapter ON knowledge_points (chapter);

CREATE TABLE users (
	id VARCHAR(36) NOT NULL,
	username VARCHAR(32) NOT NULL,
	display_name VARCHAR(64) NOT NULL,
	password_hash VARCHAR(255) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (username)
);

CREATE TABLE diagrams (
	id VARCHAR(36) NOT NULL,
	diagram_id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	title VARCHAR(200) NOT NULL,
	diagram_type VARCHAR(16) NOT NULL,
	version INTEGER NOT NULL,
	data JSON NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_diagram_version UNIQUE (diagram_id, version),
	CONSTRAINT ck_diagram_version CHECK (version >= 1),
	CONSTRAINT ck_diagram_type CHECK (diagram_type IN ('class','use_case','sequence')),
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_diagrams_owner_group ON diagrams (user_id, diagram_id);

CREATE TABLE documents (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	original_name VARCHAR(255) NOT NULL,
	storage_key VARCHAR(80) NOT NULL,
	media_type VARCHAR(100) NOT NULL,
	size_bytes INTEGER NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_document_size CHECK (size_bytes > 0),
	FOREIGN KEY(user_id) REFERENCES users (id),
	UNIQUE (storage_key)
);

CREATE INDEX ix_documents_user_id ON documents (user_id);

CREATE TABLE learning_records (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	exercise_id VARCHAR(16) NOT NULL,
	knowledge_point_ids JSON NOT NULL,
	submitted_answer JSON NOT NULL,
	is_correct BOOLEAN NOT NULL,
	activity_type VARCHAR(32) NOT NULL,
	client_event_id VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_record_event UNIQUE (user_id, client_event_id),
	FOREIGN KEY(user_id) REFERENCES users (id),
	FOREIGN KEY(exercise_id) REFERENCES exercises (id)
);

CREATE INDEX ix_records_user_created ON learning_records (user_id, created_at);

COMMIT;
