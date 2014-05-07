config.json:
	python allocate.py

v1/allocated/all: config.json
	python manage_jacuzzis.py
