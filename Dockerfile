FROM python:3.12-slim

WORKDIR /app

# Install system dependencies required by yt-dlp and ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create data directory for persistent storage
RUN mkdir -p /data

# Set environment defaults for Fly.io
ENV DB_PATH=/data/app.db
ENV HOST=0.0.0.0
ENV PORT=8080
ENV FLASK_DEBUG=false

# Run with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "120", "web:app"]
