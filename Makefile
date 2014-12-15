all: pull allocate commit push

pull:
	@git fetch -q origin
	@git reset -q --hard origin/master
	@git clean -q -f -d v1/

allocate: v1/allocated/all

commit: allocate
	@if [ -s allocate.log ]; then \
	    git add -A v1 config.json; \
	    git commit --author="allocator <no-reply@mozilla.com>" -q -F allocate.log; \
	fi

push: commit
	@git push -q origin

v1/allocated/all: config.json
	@python manage_jacuzzis.py

config.json:
	@python allocate.py --db ${DB_URL} > allocate.log

.PHONY: config.json pull allocate commit push
