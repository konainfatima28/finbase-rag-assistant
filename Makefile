# Thin wrapper: every target delegates to the cross-platform runner (Windows: use `python scripts/tasks.py <task>`).
PY ?= python
TASKS = setup audit ingest ingest-openai check-openai lint format typecheck test eval eval-retrieval calibrate dev-api dev-web web-check
.PHONY: $(TASKS)
$(TASKS):
	$(PY) scripts/tasks.py $@ $(ARGS)
