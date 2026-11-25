# Build context is the REPOSITORY ROOT:  docker build -f components/06_products_pipeline/Dockerfile -t scats-products .
# NOT BUILT in the environment where this file was written (no Docker daemon): treat it as untested.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/libs:/srv/components/02_parser_api:/srv/components/03_qc_framework:/srv/components/05_data_store:/srv/components/06_products_pipeline
WORKDIR /srv

COPY components/06_products_pipeline/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY libs ./libs
# scats_store imports scats_ingest, which imports scats_qc
COPY components/02_parser_api ./components/02_parser_api
COPY components/03_qc_framework ./components/03_qc_framework
COPY components/05_data_store ./components/05_data_store
COPY components/06_products_pipeline ./components/06_products_pipeline

RUN useradd --no-create-home --uid 10004 scats
USER scats

CMD ["python", "-m", "scats_products", "build"]
