FROM python:3.11-slim

WORKDIR /app

# Install system deps needed by sentence-transformers and chromadb
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer-cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Install the package in editable mode so entry points resolve
RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "src.empire_dispatcher.api:app", "--host", "0.0.0.0", "--port", "8000"]
