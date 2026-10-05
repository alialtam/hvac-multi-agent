# Backend + simulator image (one image, two services in docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
COPY simulator/requirements.txt simulator/requirements.txt
RUN pip install -r backend/requirements.txt -r simulator/requirements.txt

COPY contracts contracts
COPY data data
COPY simulator simulator
COPY backend backend

# train the detector on the normal training days (about 1 minute, baked into the image)
RUN cd backend && python -m app.detection.train

WORKDIR /app/backend
