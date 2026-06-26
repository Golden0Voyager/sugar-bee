REPO := asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/sugar-bee/sugar-bee
YAML := deploy/cloud-run.yaml
SHA := $(shell git rev-parse --short HEAD)

.PHONY: help build deploy deploy-quick logs clean-images test lint

help: ## Show help (default)
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build: ## Build & push → update cloud-run.yaml digest
	gcloud builds submit --tag $(REPO):latest && \
	D=$$(gcloud artifacts docker images describe $(REPO):latest \
	  --format='value(image_summary.digest)') && \
	sed -i '' 's|image: $(REPO)@sha256:[a-f0-9]\{64\}|image: $(REPO)@'"$$D"'|' $(YAML) && \
	echo "OK digest $$D written to $(YAML)"

deploy: ## gcloud run services replace
	gcloud run services replace $(YAML) --region asia-east2

deploy-quick: ## make deploy-quick DIGEST=sha256:xxx
	gcloud run deploy sugar-bee --image $(REPO)@$(DIGEST) --region asia-east2

logs: ## View recent Cloud Run logs
	gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=sugar-bee" --limit=50

clean-images: ## List old images
	gcloud artifacts docker images list $(REPO) --include-tags

test: ## Run tests
	uv run python -m pytest tests/ -q

lint: ## Run ruff check
	uv run ruff check .

fmt: ## Auto-fix lint + format
	uv run ruff check . --fix && uv run ruff format .
