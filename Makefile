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
	mkdir -p $(PCO_DIR)/google/protobuf
	protoc \
		--proto_path=$(PROTO_DIR) \
		--python_out=$(PCO_DIR) \
		$(PROTO_DIR)/*.proto
	protoc \
		--proto_path=$(PROTO_DIR) \
		--python_out=$(PCO_DIR)/google/protobuf \
		$(PROTO_DIR)/google/protobuf/wrappers.proto
	@echo "Generated $(PCO_DIR)/"

# ── clean ─────────────────────────────────────────────────────────────────────

clean:
	rm -rf $(PCO_DIR) $(VENV)

# ── help ─────────────────────────────────────────────────────────────────────

help:
	@echo "Targets:"
	@echo "  make            - setup + generate (default)"
	@echo "  make setup      - create venv, install pip deps"
	@echo "  make generate   - clone proto repo and compile pco_types/"
	@echo "  make clean      - remove pco_types/ and venv"
