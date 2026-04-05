FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl x11-xserver-utils && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py db.py models.py ./
COPY templates/ templates/
COPY static/ static/
COPY migrations/ migrations/
COPY index.html .

EXPOSE 3050

CMD ["python", "server.py"]
