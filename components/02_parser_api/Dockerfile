# Build context is the REPOSITORY ROOT (the image needs libs/ and components/03_qc_framework too):
#   docker build -f components/02_parser_api/Dockerfile -t scats-parser-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/libs:/srv/components/02_parser_api:/srv/components/03_qc_framework
WORKDIR /srv

COPY components/02_parser_api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY libs ./libs
COPY components/02_parser_api ./components/02_parser_api
COPY components/03_qc_framework ./components/03_qc_framework

RUN useradd --no-create-home --uid 10001 scats && mkdir -p /srv/staging && chown scats /srv/staging
USER scats

EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--app-dir", "/srv/components/02_parser_api", "--host", "0.0.0.0", "--port", "8001"]
