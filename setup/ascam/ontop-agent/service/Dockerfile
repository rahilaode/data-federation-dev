FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8000
# The agent needs the Docker socket to run the Ontop CLI and (F4) to reload Ontop,
# so it runs as root inside its own container.
CMD ["ascam-ontop-agent", "serve"]
