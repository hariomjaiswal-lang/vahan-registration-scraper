# VAHAN scraper - Azure App Service (Linux, custom container).
# Playwright needs real system libraries for headless Chromium that the
# standard App Service Python runtime doesn't ship with, so this has to be a
# custom container rather than App Service's built-in Python stack.
FROM python:3.12-slim

WORKDIR /app

# System deps Playwright's Chromium needs at runtime (fonts, graphics libs).
# `playwright install --with-deps` below pulls the rest; this covers the base
# packages apt needs to exist first.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates fonts-liberation libnss3 libatk-bridge2.0-0 \
    libgtk-3-0 libgbm1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY backend/ backend/
COPY frontend/ frontend/
COPY tools/ tools/
COPY data/ data/
COPY config.yaml .

# App Service for Containers sends traffic to port 8000 if WEBSITES_PORT is
# set to match (configured on the Web App, not here).
EXPOSE 8000
ENV VAHAN_HOST=0.0.0.0
ENV VAHAN_PORT=8000
ENV VAHAN_SERVE_FRONTEND=true

CMD ["python", "-m", "backend"]
