# Render (free web service): ONE container runs the broker, the simulator and the
# backend, and the backend also serves the dashboard. One link for everything.

# 1. build the dashboard for "same address, API under /api"
FROM node:22-slim AS web
WORKDIR /repo
COPY dashboard/package.json dashboard/package-lock.json dashboard/
RUN cd dashboard && npm ci
COPY contracts contracts
COPY dashboard dashboard
ENV VITE_API_URL=/api
RUN cd dashboard && npm run build

# 2. Python app + Mosquitto
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 APP_DIR=/app WEB_DIR=/app/web REQUIRE_WEB=1
RUN apt-get update && apt-get install -y --no-install-recommends mosquitto \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
COPY simulator/requirements.txt simulator/requirements.txt
RUN pip install -r backend/requirements.txt -r simulator/requirements.txt
COPY contracts contracts
COPY data data
COPY simulator simulator
COPY backend backend
COPY deploy/render/start.sh deploy/render/mosquitto.conf ./
COPY --from=web /repo/dashboard/dist /app/web
# train the detector on the normal training days (seconds; baked into the image)
RUN cd backend && python -m app.detection.train && chmod +x /app/start.sh
CMD ["/app/start.sh"]
