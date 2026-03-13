FROM public.ecr.aws/docker/library/python:3.12-slim

# Install ffmpeg, adb, and dependencies for deno
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip adb && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install deno (JS runtime for yt-dlp-ejs)
ENV DENO_VERSION=2.7.4
RUN curl -fsSL "https://dl.deno.land/release/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" -o /tmp/deno.zip && \
    unzip -o /tmp/deno.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/deno && \
    rm /tmp/deno.zip

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

# Create non-root user for default case (runtime UID/GID via PYTR_UID/PYTR_GID env vars)
RUN groupadd -g 1000 pytr && \
    useradd -u 1000 -g 1000 -s /bin/sh -M pytr
RUN mkdir -p .cache && chown 1000:1000 .cache
# ADB keys: symlink .android → data/adb so keys persist via the data volume mount
ENV HOME=/app
RUN ln -s /app/data/adb /app/.android

# Expose port
EXPOSE 8000

# Run the application — fix data dir permissions, then drop to non-root
CMD ["sh", "-c", "U=${PYTR_UID:-1000} G=${PYTR_GID:-1000} && chown $U:$G /app/data /app/.cache && mkdir -p /app/data/adb && chown $U:$G /app/data/adb && exec setpriv --reuid=$U --regid=$G --init-groups uvicorn app:app --host 0.0.0.0 --port 8000"]
