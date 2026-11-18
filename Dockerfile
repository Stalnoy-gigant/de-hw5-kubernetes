FROM python:3.12-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/common.py src/pairs.py ./
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/app/pairs.py"]
