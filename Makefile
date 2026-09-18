.PHONY: corpus census index load test api web build deploy eval demo-check smoke

CORPUS_DIR ?= corpus/serverless-patterns

corpus:
	git clone --depth 1 https://github.com/aws-samples/serverless-patterns $(CORPUS_DIR)
	cd $(CORPUS_DIR) && git rev-parse HEAD > ../../data/corpus_commit.txt

census:
	python -m indexer.census

index:
	python -m indexer.build_index

load:
	python -m indexer.load_ddb

test:
	python -m pytest

api:
	python -m uvicorn api.app:app --host 127.0.0.1 --port 8000 --reload

web:
	cd web && npm run dev

build:
	sam build --template infra/template.yaml

deploy:
	sam deploy --template infra/template.yaml

eval:
	python -m eval.run_all

demo-check:
	python -m eval.demo_check

smoke:
	python -m eval.smoke
