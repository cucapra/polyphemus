.PHONY: run clean

PORT ?= 8000

dev:
	FLASK_APP=polyphemus.server FLASK_ENV=development pipenv run flask run

serve:
	pipenv run gunicorn --bind 0.0.0.0:$(PORT) polyphemus.server:app

worker:
	pipenv run python -m polyphemus.workproc

clean:
	rm -rf instance
