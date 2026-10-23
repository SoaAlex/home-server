FROM mcr.microsoft.com/playwright/python:v1.48.0-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app TZ=Europe/Paris
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY . /app
USER 1000:10
CMD ["python", "-m", "eurl_frais"]
