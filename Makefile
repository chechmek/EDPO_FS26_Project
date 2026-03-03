PYTHON ?= python3.12
VENV_PYTHON = .venv/bin/python
UV = .venv/bin/uv
TARGET_USER = 11111111-1111-4111-8111-111111111111

.PHONY: venv install up down demo

venv:
	$(PYTHON) -m venv .venv
	$(VENV_PYTHON) -m pip install --upgrade pip uv

install:
	$(UV) pip install -e ./shared -e ./services/post-service -e ./services/interaction-service -e ./services/notification-service requests

up:
	docker compose up -d --build

down:
	docker compose down -v --remove-orphans

demo:
	$(VENV_PYTHON) scripts/generate_load.py --posts 10 --users 20 --events 200 --target-user $(TARGET_USER)
