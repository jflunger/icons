# Local (non-vibe) convenience targets. Under vibe, run.sh does setup and serving.
PY := .venv/bin/python

setup:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

ingest:
	$(PY) -m iconsearch.ingest

serve:
	.venv/bin/uvicorn iconsearch.server:app --host 127.0.0.1 --port 8765

test:
	$(PY) -m pytest -q

check-svgs:
	$(PY) scripts/check_svgs.py

.PHONY: setup ingest serve test check-svgs
