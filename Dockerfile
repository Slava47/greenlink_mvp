FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Where SQLite DB and uploads will live
RUN mkdir -p /app/data

EXPOSE 8000

# Create DB + seed on first run (default)
ENV DB_PATH=/app/data/app.db
ENV SEED_ON_FIRST_RUN=1

CMD ["gunicorn", "-b", "0.0.0.0:8000", "wsgi:app"]
