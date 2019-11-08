.PHONY: run clean serve worker

PORT ?= 8000

dev:
	FLASK_APP=polyphemus.server FLASK_ENV=development pipenv run flask run --no-reload

serve:
	pipenv run gunicorn --bind 0.0.0.0:$(PORT) -k eventlet --workers 4 --worker-connections 100 polyphemus.server:app

clean:
	rm -rf instance

worker:
	pipenv run worker
