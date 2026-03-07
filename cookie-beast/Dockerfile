FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends firefox-esr wget && \
    rm -rf /var/lib/apt/lists/*

# Install geckodriver
RUN GECKO_VER=$(wget -qO- https://api.github.com/repos/mozilla/geckodriver/releases/latest | \
    python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])") && \
    wget -q "https://github.com/mozilla/geckodriver/releases/download/${GECKO_VER}/geckodriver-${GECKO_VER}-linux64.tar.gz" && \
    tar xzf geckodriver-*.tar.gz -C /usr/local/bin/ && \
    rm geckodriver-*.tar.gz

RUN pip install --no-cache-dir selenium undetected-geckodriver

RUN groupadd -g 1000 beast && \
    useradd -m -u 1000 -g 1000 beast

WORKDIR /app
COPY cookied.py diag.py humanize.py ./

ENV HOME=/home/beast

CMD ["sh", "-c", "U=${PYTR_UID:-1000} G=${PYTR_GID:-1000} && chown $U:$G /data /home/beast && exec setpriv --reuid=$U --regid=$G --init-groups python -u cookied.py"]
