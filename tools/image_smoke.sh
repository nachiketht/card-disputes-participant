#!/bin/sh
# Build the local image and run one case through the loopback mock.
# Does not push. Does not publish a host port.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$root"
tag=${1:-card-disputes:ci}
mock_name=card-disputes-smoke-mock-$$

cleanup() {
  docker rm -f "$mock_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build -t "$tag" .
docker rm -f "$mock_name" >/dev/null 2>&1 || true
docker run -d --name "$mock_name" \
  -v "$root/data/dev/dataset.json:/data/dataset.json:ro" \
  -v "card-disputes-smoke-state-$$:/state" \
  "$tag" \
  python tools/runtime.py serve --data /data --state /state/run.sqlite --port 8765

ready=0
i=0
while [ "$i" -lt 30 ]; do
  if docker exec "$mock_name" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=1).read()" >/dev/null 2>&1; then
    ready=1
    break
  fi
  i=$((i + 1))
  sleep 0.5
done
if [ "$ready" -ne 1 ]; then
  echo "mock inside the image did not answer on 127.0.0.1:8765" >&2
  docker logs "$mock_name" >&2 || true
  exit 1
fi

docker run --rm --network "container:$mock_name" \
  -v "$root/data/dev/documents:/data/documents:ro" \
  -v "$root/data/dev/policies:/data/policies:ro" \
  -e DATA_DIR=/data \
  -e MOCK_BASE_URL=http://127.0.0.1:8765 \
  -e SUBMISSIONS_DIR=/tmp/submissions \
  -e REPORTS_DIR=/tmp/reports \
  -e LLM_PROVIDER=null \
  -e FEATURE_LLM_REVIEW=off \
  "$tag" \
  dispute-copilot run CARD-D01

echo "image smoke passed ($tag)"
