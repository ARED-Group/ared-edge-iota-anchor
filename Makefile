# =============================================================================
# ARED Edge IOTA Anchor Service - Makefile
# =============================================================================

.PHONY: help install test lint format build docker-build deploy-dev clean

SHELL := /bin/bash
.DEFAULT_GOAL := help

PROJECT_NAME := ared-iota-anchor
VERSION := $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
DOCKER_REGISTRY ?= ghcr.io/ared
DOCKER_TAG ?= $(VERSION)
VENV := .venv
PYTHON := $(VENV)/bin/python

CYAN := \033[36m
GREEN := \033[32m
RESET := \033[0m

help: ## Show this help
	@echo ""
	@echo "$(CYAN)ARED Edge IOTA Anchor Service$(RESET)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ { printf "  $(CYAN)%-20s$(RESET) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""

# Development
install: ## Install dependencies
	@echo "$(CYAN)Installing dependencies...$(RESET)"
	python -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e ".[dev]"
	@echo "$(GREEN)Done$(RESET)"

dev-up: ## Start local development
	docker compose up -d

dev-down: ## Stop local development
	docker compose down -v

# Testing
test: ## Run tests
	$(PYTHON) -m pytest tests/ -v --cov=src --cov-report=term

test-unit: ## Run unit tests
	$(PYTHON) -m pytest tests/unit -v

test-integration: ## Run integration tests
	$(PYTHON) -m pytest tests/integration -v

# Code Quality
lint: ## Run linter
	$(VENV)/bin/ruff check src/ tests/
	$(VENV)/bin/mypy src/

format: ## Format code
	$(VENV)/bin/black src/ tests/
	$(VENV)/bin/ruff check --fix src/ tests/

# Build
build: ## Build package
	$(PYTHON) -m build

docker-build: ## Build Docker image
	docker build -t $(DOCKER_REGISTRY)/iota-anchor:$(DOCKER_TAG) .

docker-push: ## Push Docker image
	docker push $(DOCKER_REGISTRY)/iota-anchor:$(DOCKER_TAG)

# Deploy
deploy-dev: ## Deploy to development
	kubectl apply -k k8s/dev/ -n ared-edge

deploy-prod: ## Deploy to production
	kubectl apply -k k8s/prod/ -n ared-edge

# Cleanup
clean: ## Clean build artifacts
	rm -rf $(VENV) .pytest_cache .coverage htmlcov dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
