
CREATE TABLE audits (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	actor VARCHAR(100), 
	action VARCHAR(200), 
	created VARCHAR(40), 
	PRIMARY KEY (id)
)

;


CREATE TABLE pivots (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(100), 
	row_dim VARCHAR(30), 
	col_dim VARCHAR(30), 
	state VARCHAR(20), 
	result JSON, 
	error TEXT, 
	PRIMARY KEY (id)
)

;


CREATE TABLE settlements (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	code VARCHAR(60) NOT NULL, 
	day VARCHAR(10), 
	venue VARCHAR(40), 
	play VARCHAR(60), 
	channel VARCHAR(30), 
	gross_cents BIGINT, 
	refund_cents BIGINT, 
	fee_cents BIGINT, 
	seats INTEGER, 
	checked INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (code)
)

;


CREATE TABLE users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	email VARCHAR(100) NOT NULL, 
	password VARCHAR(200), 
	`role` VARCHAR(20), 
	PRIMARY KEY (id), 
	UNIQUE (email)
)

;


CREATE TABLE reviews (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	pivot_id INTEGER, 
	row_key VARCHAR(100), 
	col_key VARCHAR(100), 
	book_cents BIGINT, 
	expected_cents BIGINT, 
	difference_cents BIGINT, 
	note TEXT, 
	status VARCHAR(20), 
	PRIMARY KEY (id), 
	FOREIGN KEY(pivot_id) REFERENCES pivots (id)
)

;
