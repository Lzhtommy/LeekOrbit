FROM python:3.11-slim
ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv export --no-dev --no-emit-project -o /tmp/req.txt && \
    pip install --no-cache-dir -r /tmp/req.txt
COPY leekorbit ./leekorbit
COPY config ./config
RUN pip install --no-cache-dir --no-deps .
CMD ["leekorbit", "run"]
