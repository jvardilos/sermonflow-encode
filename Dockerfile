# ── Stage 1: generate pco_types ───────────────────────────────────────────────
FROM python:3.11 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    protobuf-compiler \
    git \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt Makefile ./
RUN make generate
RUN make rmrepo

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=builder /build/pco_types ./pco_types
COPY encode.py decode.py helpers.py main.py ./

CMD ["python", "main.py"]
