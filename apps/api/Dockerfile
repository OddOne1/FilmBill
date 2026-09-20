FROM python:3.11-slim

WORKDIR /workspace

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install API dependencies
COPY apps/api/requirements.txt /workspace/apps/api/requirements.txt
RUN pip install --no-cache-dir -r /workspace/apps/api/requirements.txt

COPY apps/__init__.py /workspace/apps/__init__.py
COPY apps/api /workspace/apps/api

# Stay in /workspace so imports work as packages
WORKDIR /workspace

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
