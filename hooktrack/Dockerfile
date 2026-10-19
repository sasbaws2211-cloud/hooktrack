FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /hooktrack
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
COPY admin ./admin
COPY static ./static
# Same image runs either service. Default = customer app; for the admin set the start command to:
#   python -m uvicorn admin.main:app --host 0.0.0.0 --port $PORT
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8030}"] 
CMD ["sh", "-c", "python -m uvicorn admin.main:app --host 0.0.0.0 --port $PORT"]
