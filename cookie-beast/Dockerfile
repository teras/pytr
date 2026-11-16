FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends firefox-esr wget && \
    rm -rf /var/lib/apt/lists/*

# Pin the driver so builds do not depend on GitHub's unauthenticated API quota.
ARG GECKODRIVER_VERSION=v0.37.1
RUN wget --no-verbose --tries=3 --timeout=30 -O /tmp/geckodriver.tar.gz \
    "https://github.com/mozilla/geckodriver/releases/download/${GECKODRIVER_VERSION}/geckodriver-${GECKODRIVER_VERSION}-linux64.tar.gz" && \
    tar xzf /tmp/geckodriver.tar.gz -C /usr/local/bin/ && \
    geckodriver --version && \
    rm /tmp/geckodriver.tar.gz

RUN pip install --no-cache-dir selenium undetected-geckodriver

RUN groupadd -g 1000 beast && \
    useradd -m -u 1000 -g 1000 beast

WORKDIR /app
COPY cookied.py diag.py humanize.py ./

ENV HOME=/home/beast

CMD ["sh", "-c", "U=${PYTR_UID:-1000} G=${PYTR_GID:-1000} && chown $U:$G /data /home/beast && exec setpriv --reuid=$U --regid=$G --init-groups python -u cookied.py"]
