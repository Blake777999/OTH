#!/usr/bin/env bash
set -e

# Change to the project directory
cd "$(dirname "$0")"

# Ensure venv exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
fi

echo "=================================================="
echo " Starting Old Town Hours Scheduling System"
echo " Web UI: http://localhost:8000"
echo " API Docs: http://localhost:8000/docs"
echo "=================================================="

exec ./venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
