FROM python:3.12-slim

# Install ffmpeg and dependencies for deno
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip && \
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

# Create writable cache directory (subtitle VTTs)
ARG UID=1000
ARG GID=1000
RUN mkdir -p cache && chown ${UID}:${GID} cache

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
