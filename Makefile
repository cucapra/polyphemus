.PHONY: run clean

dev:
	FLASK_APP=polyphemus.server FLASK_ENV=development pipenv run flask run

serve:
	pipenv run gunicorn --bind 0.0.0.0:8000 polyphemus.server:app

clean:
	rm -rf instance
