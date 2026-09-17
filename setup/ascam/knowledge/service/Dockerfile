FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ASCAM_KNOWLEDGE_ALEMBIC_INI=/app/alembic.ini
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --create-home --uid 10001 ascam
USER ascam

EXPOSE 8000
# Bawaan: Knowledge Service. Layanan migrasi memakai perintah `alembic upgrade head`.
CMD ["ascam-knowledge", "serve"]
