.PHONY: up down logs test ingest eval loadtest smoke

up:
	test -f .env || cp .env.example .env
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

test:
	python -m pytest -q

ingest:
	docker compose --profile tools run --rm ingestion python -m ingestion.cli build

eval:
	python eval/run_eval.py

loadtest:
	locust -f loadtest/locustfile.py --host http://localhost:8001

smoke:
	./scripts/smoke.sh
