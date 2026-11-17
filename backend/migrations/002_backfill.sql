-- 既有部署的增量 DDL（无在线迁移执行器，需手工执行一次）。
-- 新部署直接用 001_initial.sql 或 ORM create_all，无需执行本文件。
-- 缺失票款用 gross_cents IS NULL 表示，与真实零元 0 严格区分。

ALTER TABLE settlements ADD COLUMN rev INTEGER NOT NULL DEFAULT 0;
UPDATE settlements SET rev = 1 WHERE rev = 0;
-- 旧数据若把缺失当成 0 录入，需人工确认后再置空，切勿批量改写：
-- UPDATE settlements SET gross_cents = NULL WHERE code = 'YM-xxx';

CREATE TABLE vouchers (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(60) NOT NULL,
	total_cents BIGINT NOT NULL,
	note TEXT,
	rev INTEGER NOT NULL DEFAULT 0,
	created VARCHAR(40),
	updated VARCHAR(40),
	PRIMARY KEY (id),
	UNIQUE (code)
);

CREATE TABLE voucher_covers (
	voucher_id INTEGER NOT NULL,
	settlement_id INTEGER NOT NULL,
	PRIMARY KEY (voucher_id, settlement_id),
	FOREIGN KEY(voucher_id) REFERENCES vouchers (id),
	FOREIGN KEY(settlement_id) REFERENCES settlements (id),
	INDEX ix_voucher_covers_settlement_id (settlement_id)
);

CREATE TABLE backfills (
	id INTEGER NOT NULL AUTO_INCREMENT,
	name VARCHAR(100),
	state VARCHAR(20),
	run_token VARCHAR(64),
	attempts INTEGER NOT NULL DEFAULT 0,
	result JSON,
	error TEXT,
	created VARCHAR(40),
	created_by VARCHAR(100),
	solved_at VARCHAR(40),
	posted_at VARCHAR(40),
	PRIMARY KEY (id)
);
