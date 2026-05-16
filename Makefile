.PHONY: help install services voice agent app generate build clean test fmt

PY := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PYTHON := $(VENV)/bin/python

help:
	@echo "Targets:"
	@echo "  install     create .venv and install Python deps"
	@echo "  services    run voice_service and agent_service together"
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

services:
	@trap 'kill 0' INT; \
	$(PYTHON) -m voice_service & \
	$(PYTHON) -m agent_service & \
	wait

voice:
	$(PYTHON) -m voice_service

agent:
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
