SHELL := /bin/bash

VENV_DIR := $(CURDIR)/.venv
VENV_PY := $(VENV_DIR)/bin/python3
VENV_PIP := $(VENV_DIR)/bin/pip
setup:
	$(VENV_PY) -m venv $(VENV_DIR)
	source $(VENV_DIR)/bin/activate
	$(VENV_PIP) install -e ".[all,test]"

test: setup
	$(VENV_PY) -m pytest tests/ -v

clean:
	rm -rf $(VENV_DIR)