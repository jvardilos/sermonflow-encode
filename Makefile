PROTO_REPO := https://github.com/greyshirtguy/ProPresenter7-Proto
PROTO_DIR  := ProPresenter7-Proto/proto
PCO_DIR    := pco_types
VENV       := deps

.PHONY: all setup generate clean help

all: generate

# ── setup: venv + pip deps ────────────────────────────────────────────────────

setup: $(VENV)/bin/activate

$(VENV)/bin/activate: requirements.txt
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip -q
	$(VENV)/bin/pip install -r requirements.txt -q
	@touch $@

# ── proto: clone repo ─────────────────────────────────────────────────────────

ProPresenter7-Proto:
	git clone $(PROTO_REPO)

# ── generate: protoc → pco_types/ ─────────────────────────────────────────────

generate: setup ProPresenter7-Proto
	mkdir -p $(PCO_DIR)
	protoc \
		--proto_path=$(PROTO_DIR) \
		--python_out=$(PCO_DIR) \
		$(PROTO_DIR)/*.proto
	# Rewrite bare sibling imports to package-relative so pco_types works as a package
	find $(PCO_DIR) -maxdepth 1 -name "*_pb2.py" -exec sed -i '' \
		's/^import \([a-zA-Z_]*_pb2\) as/from pco_types import \1 as/' {} \;
	touch $(PCO_DIR)/__init__.py
	@echo "Generated $(PCO_DIR)/"

# ── clean ─────────────────────────────────────────────────────────────────────

clean:
	rm -rf $(PCO_DIR) $(VENV)

distclean: clean
	rm -rf ProPresenter7-Proto

# ── help ─────────────────────────────────────────────────────────────────────

help:
	@echo "Targets:"
	@echo "  make              - setup + generate (default)"
	@echo "  make setup        - create venv, install pip deps"
	@echo "  make generate     - clone proto repo and compile pco_types/"
	@echo "  make clean        - remove pco_types/ and venv"
	@echo "  make distclean    - clean + remove cloned proto repo"
