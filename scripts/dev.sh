#!/usr/bin/env bash
# One command to run the whole live stack for the demo / for watching progress.
#   - FastAPI (uvicorn) on :8000  with autoreload
#   - Vite dev server on :5173    with hot module reload  (open this one)
set -e
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi
if [ ! -d dashboard/node_modules ]; then
  (cd dashboard && npm install --silent)
fi

./.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload &
API_PID=$!
trap "kill $API_PID 2>/dev/null" EXIT

cd dashboard
exec npm run dev
