FROM python:3.12-slim

# Install ffmpeg, adb, and dependencies for deno
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip adb && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install deno (required by yt-dlp for YouTube extraction)
ENV DENO_INSTALL="/usr/local/deno"
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$DENO_INSTALL" sh
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY web/ .

# Copy pre-built TV client packages (from build/ directory)
COPY build/pytr-tv.ipk clients/webos/pytr-tv.ipk
COPY build/pytr-tv.apk clients/android/pytr-tv.apk

# Create writable directories
ARG UID=1000
ARG GID=1000
RUN mkdir -p cache && chown ${UID}:${GID} cache
# ADB keys: symlink .android → data/adb so keys persist via the data volume mount
ENV HOME=/app
RUN ln -s /app/data/adb /app/.android

# Expose port
EXPOSE 8000

# Run the application — ensure ADB key directory exists at startup
CMD ["sh", "-c", "mkdir -p /app/data/adb && exec uvicorn app:app --host 0.0.0.0 --port 8000"]
