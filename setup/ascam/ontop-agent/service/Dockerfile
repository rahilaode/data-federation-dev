FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8000
# Agen memerlukan akses Docker socket untuk menjalankan CLI Ontop dan (F4) memuat ulang Ontop,
# sehingga dijalankan sebagai root di dalam kontainernya sendiri.
CMD ["ascam-ontop-agent", "serve"]
