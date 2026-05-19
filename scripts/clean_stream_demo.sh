#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

AUTO_START=true
if [[ "${1:-}" == "--no-start" ]]; then
  AUTO_START=false
fi

echo "[clean] stopping stream demo services"
docker compose stop ui content-ledger sla-monitor notification-service >/dev/null 2>&1 || true
docker compose rm -f ui content-ledger sla-monitor notification-service >/dev/null 2>&1 || true

echo "[clean] removing kafka infra containers"
docker compose -f docker-compose.infra.yml down -v >/dev/null 2>&1 || true

if [[ "$AUTO_START" == "false" ]]; then
  echo "[clean] complete"
  echo "[clean] kafka history and processor state cleared; services not restarted (--no-start)"
  exit 0
fi

echo "[clean] starting fresh kafka infra"
docker compose -f docker-compose.infra.yml up -d

echo "[clean] starting fresh stream demo services"
docker compose up -d --build notification-service sla-monitor content-ledger ui

echo "[clean] done"
echo "[clean] ui: http://localhost:8086"
echo "[clean] content-ledger api: http://localhost:8085"
echo "[clean] sla-monitor api: http://localhost:8005"
