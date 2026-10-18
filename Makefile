.PHONY: install lint type test check infra demo serve web

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy

test:
	uv run pytest

check: lint type test
	uv run python scripts/check_docs.py
	uv run d2d simulate --data-dir data/check
	uv run d2d run --data-dir data/check
	uv run d2d eval changelog --data-dir data/check
	uv run d2d eval linking --data-dir data/check
	uv run d2d security scan --data-dir data/check
	uv run d2d eval security --data-dir data/check
	uv run d2d export warehouse --no-db --data-dir data/check
	uv run d2d review export --no-db --data-dir data/check
	uv run d2d train --training-set data/check/labels/training_set.jsonl --models-dir data/check/models
	uv run d2d eval classifier --models-dir data/check/models
	cd web && npm ci --no-audit --no-fund && npm test && npm run lint && npm run build

# Terraform (fmt, validate, mocked tests) and an Ansible syntax check; no cloud credentials needed.
infra:
	terraform -chdir=infra/terraform fmt -check -recursive
	for m in gcp aws; do \
	  terraform -chdir=infra/terraform/$$m init -backend=false -input=false >/dev/null && \
	  terraform -chdir=infra/terraform/$$m validate && \
	  terraform -chdir=infra/terraform/$$m test || exit 1; \
	done
	uvx --from ansible-core ansible-playbook -i infra/ansible/inventory.example.ini \
	  infra/ansible/site.yml --syntax-check

demo:
	uv run d2d simulate
	uv run d2d run
	uv run d2d eval changelog
	uv run d2d eval linking

serve:
	uv run d2d serve --data-dir data

web:
	cd web && npm run dev
