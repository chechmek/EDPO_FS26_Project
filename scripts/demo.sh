#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TARGET_USER="11111111-1111-4111-8111-111111111111"

docker compose up -d --build

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv. Run: make venv && make install"
  exit 1
fi

.venv/bin/python scripts/generate_load.py --posts 10 --users 20 --events 200 --target-user "$TARGET_USER"

echo
echo "Sample unread notifications for $TARGET_USER:"
curl -s "http://localhost:8003/users/$TARGET_USER/notifications?unread_only=true"
echo
