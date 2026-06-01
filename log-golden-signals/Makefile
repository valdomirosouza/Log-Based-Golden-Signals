.PHONY: build up down logs test test-integration seed

COMPOSE = docker compose -f docker-compose.yml

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d
	@echo "Waiting for services to be healthy..."
	@sleep 5

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

test:
	$(COMPOSE) run --rm ingestion_api python -m pytest /tests -v

test-integration:
	python scripts/test_integration.py

seed: up
	python scripts/seed.py
