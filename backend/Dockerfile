FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/shared

WORKDIR /app

# Install dependencies first (cached layer), then the source code.
COPY pyproject.toml ./
COPY app/__init__.py app/__init__.py
RUN pip install ".[dev,report]"

# Anonymous speaker voiceprints (docs/SPEAKERS.md): 3D-Speaker CAM++ VoxCeleb model, Apache-2.0, ~30 MB
ENV SPEAKER_MODEL_PATH=/models/speaker-campplus.onnx
RUN mkdir -p /models && python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx', '/models/speaker-campplus.onnx')"

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
