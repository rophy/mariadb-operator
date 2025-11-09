FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY main.py .

# Make script executable
RUN chmod +x main.py

# Run as non-root user
RUN useradd -m -u 1000 testclient && \
    chown -R testclient:testclient /app

USER testclient

ENTRYPOINT ["python", "main.py"]
