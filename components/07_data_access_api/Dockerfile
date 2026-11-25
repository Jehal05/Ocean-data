# Build context is the REPOSITORY ROOT (the image needs libs/ and the store library):
#   docker build -f components/07_data_access_api/Dockerfile -t scats-access-api .
# NOT BUILT in the environment where this file was written (no Docker daemon): treat it as untested.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/libs:/srv/components/02_parser_api:/srv/components/03_qc_framework:/srv/components/05_data_store:/srv/components/07_data_access_api
WORKDIR /srv

COPY components/07_data_access_api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY libs ./libs
COPY components/05_data_store ./components/05_data_store
# scats_store imports scats_ingest, which imports scats_qc
COPY components/02_parser_api ./components/02_parser_api
COPY components/03_qc_framework ./components/03_qc_framework
COPY components/07_data_access_api ./components/07_data_access_api

RUN useradd --no-create-home --uid 10003 scats
USER scats

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "/srv/components/07_data_access_api", "--host", "0.0.0.0", "--port", "8000"]
