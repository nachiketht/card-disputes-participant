# One image for the two containers. Case files are not copied in.
# The mock container mounts dataset.json. The app container mounts documents/ and policies/.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY UI ./UI
COPY tools ./tools
COPY config ./config
COPY prompts ./prompts

RUN pip install --no-cache-dir .
RUN ln -s /app/config /usr/local/lib/python3.12/config \
 && ln -s /app/prompts /usr/local/lib/python3.12/prompts

ENV PYTHONUNBUFFERED=1 \
    MOCK_BASE_URL=http://127.0.0.1:8765 \
    DATA_DIR=/data \
    SUBMISSIONS_DIR=/submissions

# Default process is the mock. Compose and Azure override the app container.
CMD ["python", "tools/runtime.py", "serve", "--data", "/data", "--state", "/state/run.sqlite", "--port", "8765"]
