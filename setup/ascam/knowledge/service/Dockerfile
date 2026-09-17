FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --create-home --uid 10001 ascam
USER ascam

# Bawaan: jalankan migrasi sampai revisi terbaru
CMD ["alembic", "upgrade", "head"]
