.PHONY: help install hermes-deps hermes-patch services services-detached voice agent app generate build clean test fmt

PY := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PYTHON := $(VENV)/bin/python

ifneq (,$(wildcard .env))
include .env
export
endif

help:
	@echo "Targets:"
	@echo "  install     create .venv and install Python deps"
	@echo "  hermes-deps install/patch Hermes computer_use runtime deps"
	@echo "  services    run voice_service and agent_service together"
	@echo "  services-detached run helpers in the background with logs"
	@echo "  voice       run voice_service only"
	@echo "  agent       run agent_service only"
	@echo "  generate    regenerate Xcode project (requires xcodegen)"
	@echo "  app         build the macOS app .app bundle"
	@echo "  test        run Python test suite"
	@echo "  fmt         format Python with ruff"
	@echo "  clean       remove build artifacts and caches"

$(VENV)/bin/python:
	$(PY) -m venv $(VENV)

install: $(VENV)/bin/python
	$(PIP) install -U pip wheel
	$(PIP) install -r voice_service/requirements.txt
	$(PIP) install -r agent_service/requirements.txt
	$(PIP) install -r tests/requirements.txt

hermes-deps:
	@test -x .venv-hermes/bin/pip || (echo ".venv-hermes is missing; install Hermes first" >&2; exit 1)
	.venv-hermes/bin/pip install mcp websockets
	.venv-hermes/bin/python scripts/patch_hermes_computer_use.py

hermes-patch:
	@if [ -x .venv-hermes/bin/python ]; then \
		.venv-hermes/bin/python scripts/patch_hermes_computer_use.py; \
	else \
		echo ".venv-hermes not found; skipping Hermes computer_use patch"; \
	fi

services: hermes-patch
	@mkdir -p logs
	@: > logs/voice_service.log
	@: > logs/agent_service.log
	@trap 'kill 0' INT; \
	YUME_LOG_FILE=logs/voice_service.log $(PYTHON) -m voice_service & \
	YUME_LOG_FILE=logs/agent_service.log $(PYTHON) -m agent_service & \
	wait

services-detached: hermes-patch
	/bin/bash scripts/run_services_detached.sh

voice:
	$(PYTHON) -m voice_service

agent: hermes-patch
	$(PYTHON) -m agent_service

generate:
	cd app && xcodegen generate

app: generate
	cd app && xcodebuild -project yume.xcodeproj -scheme yume -configuration Debug -derivedDataPath build CODE_SIGN_IDENTITY="-" CODE_SIGNING_REQUIRED=NO build
	@echo "App at app/build/Build/Products/Debug/yume.app"

test:
	$(PYTHON) -m pytest tests/ -v

fmt:
	$(PYTHON) -m ruff format voice_service agent_service tests
	$(PYTHON) -m ruff check --fix voice_service agent_service tests

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache
	rm -rf app/build app/yume.xcodeproj app/.build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
