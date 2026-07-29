.PHONY: install test lint run up down

install:
	python -m pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check .

run:
	uvicorn app.main:app --reload

up:
	docker compose up --build -d

down:
	docker compose down
