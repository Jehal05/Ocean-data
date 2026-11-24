# Build context is the REPOSITORY ROOT (the image needs libs/, the ingest and QC packages and the store library):
#   docker build -f components/04_expert_review_ui/Dockerfile -t scats-review-api .
# NOT BUILT in the environment where this file was written (no Docker daemon): treat it as untested.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/libs:/srv/components/02_parser_api:/srv/components/03_qc_framework:/srv/components/04_expert_review_ui:/srv/components/05_data_store
WORKDIR /srv

COPY components/04_expert_review_ui/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY libs ./libs
COPY components/02_parser_api ./components/02_parser_api
COPY components/03_qc_framework ./components/03_qc_framework
COPY components/04_expert_review_ui ./components/04_expert_review_ui
COPY components/05_data_store ./components/05_data_store

RUN useradd --no-create-home --uid 10002 scats
USER scats

EXPOSE 8002
CMD ["uvicorn", "review_api.main:app", "--app-dir", "/srv/components/04_expert_review_ui", "--host", "0.0.0.0", "--port", "8002"]
