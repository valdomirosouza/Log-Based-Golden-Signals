.PHONY: build up down logs test test-integration seed

COMPOSE = docker compose -f docker-compose.yml

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d
	@echo "Waiting for services to be healthy..."
	@sleep 8

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

test:
	uv run --with fastapi --with httpx --with pytest --with python-json-logger \
	  --with "redis>=5.2.1" --with "aiohttp>=3.11" \
	  pytest tests/test_ingestion.py tests/test_processor.py tests/test_analytics.py -v

test-integration: up
	@echo "Running integration tests (requires running stack)..."
	python3 tests/test_integration.py

seed: up
	python3 scripts/seed.py
