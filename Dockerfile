FROM python:3.11-slim

# One container = one server process; each call runs its own pipeline
# (pipecat WorkerRunner). Scale horizontally; do not multiplex calls in one
# process beyond dev loads (see PLAN.md section 8).

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY va ./va
COPY apps ./apps
COPY config ./config
RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["python", "-m", "apps.server"]
