FROM public.ecr.aws/docker/library/python:3.12-slim

# yt-dlp needs ffmpeg for postprocessing; Deno for yt-dlp-ejs JS challenges.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV DENO_VERSION=2.7.4
RUN curl -fsSL "https://dl.deno.land/release/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" -o /tmp/deno.zip && \
    unzip -o /tmp/deno.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/deno && \
    rm /tmp/deno.zip

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/

# Non-root user; runtime UID/GID via env vars match PYTR's data ownership.
RUN groupadd -g 1000 foryou && \
    useradd -u 1000 -g 1000 -s /bin/sh -M foryou
RUN mkdir -p .cache && chown 1000:1000 .cache
ENV HOME=/app

EXPOSE 8000

CMD ["sh", "-c", "U=${PYTR_UID:-1000} G=${PYTR_GID:-1000} && chown -R $U:$G /app/data 2>/dev/null || true && exec setpriv --reuid=$U --regid=$G --init-groups uvicorn app.main:app --host 0.0.0.0 --port 8000"]
