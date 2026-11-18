FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Offline voice model (~40 MB), served from the same origin so phones don't fetch it from GitHub Pages.
ADD https://ccoreilly.github.io/vosk-browser/models/vosk-model-small-en-us-0.15.tar.gz public/models/
COPY app app
COPY public public
ENV DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
