v1/allocated/all: config.json
	python manage_jacuzzis.py

config.json:
	python allocate.py

.PHONY: config.json
