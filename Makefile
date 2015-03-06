all: allocate

allocate: v1/allocated/all

v1/allocated/all: config.json
	@python manage_jacuzzis.py

config.json:
	@python allocate.py --db ${DB_URL} > allocate.log

.PHONY: config.json allocate
