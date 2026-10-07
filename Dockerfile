# Mesh-Citadel BBS. Mount a directory with config.yaml at /data; point
# database.db_path there so the database survives container rebuilds.
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --system --uid 10001 --home-dir /data citadel \
    && mkdir -p /data && chown citadel /data
USER citadel

VOLUME ["/data"]
CMD ["python", "main.py", "-c", "/data/config.yaml"]
