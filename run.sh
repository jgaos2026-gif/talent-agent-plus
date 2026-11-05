#!/bin/sh
cd "$(dirname "$0")"
exec python3 server.py --host 0.0.0.0 --port "${PORT:-8080}"
