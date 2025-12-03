# Contributing to ARED Edge IOTA Anchor

## Development Setup

### Prerequisites
- Python 3.11+
- Docker and Docker Compose
- Access to IOTA testnet

### Quick Start

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Start local environment
docker compose up -d
```

## Coding Standards

- Follow PEP 8 (enforced by Black)
- Use type hints for all functions
- Document all public APIs
- Maintain >80% test coverage

## Testing

```bash
# Unit tests
pytest tests/unit

# Integration tests (requires IOTA testnet)
pytest tests/integration

# Coverage report
pytest --cov=src --cov-report=html
```

## Commit Guidelines

Use Conventional Commits:
```
feat(aggregator): add Merkle tree caching
fix(poster): handle network timeout
docs(api): update verification endpoint docs
```

## Pull Request Process

1. Create feature branch
2. Write tests
3. Run `make lint` and `make test`
4. Submit PR with clear description
