FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLAGS_use_mkldnn=0 \
    FLAGS_enable_onednn=0 \
    FLAGS_enable_pir_in_executor=0 \
    FLAGS_enable_pir_api=0 \
    FLAGS_new_executor=0 \
    FLAGS_use_new_executor=0 \
    FLAGS_USE_STANDALONE_EXECUTOR=false

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential libpq-dev pkg-config libcairo2-dev libffi-dev fonts-dejavu-core \
    libgl1 libglib2.0-0 libgomp1 \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x docker/run_web.sh

# For Dokku: uses $PORT
CMD ["sh", "/app/docker/run_web.sh"]
