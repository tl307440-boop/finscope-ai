#!/usr/bin/env bash
# 在服务器上执行：加载新镜像标签、起服务、健康检查，失败则回滚到上一个版本。
# 用法： deploy.sh <IMAGE_TAG>
set -euo pipefail

TAG="${1:?用法: deploy.sh <IMAGE_TAG>}"
APP_DIR="${APP_DIR:-/opt/finscope}"
cd "$APP_DIR"

[ -f .env ] || { echo "缺少 $APP_DIR/.env，请先按 env.production.example 创建" >&2; exit 2; }

COMPOSE=(docker compose -f docker-compose.prod.yml --env-file .env --env-file .release.env)
WEB_PORT="$(grep -E '^FINSCOPE_WEB_PORT=' .env | tail -n1 | cut -d= -f2 || true)"
WEB_PORT="${WEB_PORT:-80}"

PREV_TAG=""
[ -f .release.env ] && PREV_TAG="$(grep -E '^IMAGE_TAG=' .release.env | cut -d= -f2 || true)"

wait_healthy() {
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${WEB_PORT}/api/v1/health/live" >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  return 1
}

echo "IMAGE_TAG=${TAG}" > .release.env
"${COMPOSE[@]}" up -d --remove-orphans

if wait_healthy; then
  echo "部署成功：${TAG}"
  # 只保留最近 3 个版本的镜像，防止磁盘被占满
  for repo in finscope-backend finscope-frontend; do
    docker images "$repo" --format '{{.Tag}}' | tail -n +4 | xargs -r -I{} docker rmi "$repo:{}" || true
  done
  exit 0
fi

echo "健康检查失败，最近日志：" >&2
"${COMPOSE[@]}" logs --tail=60 api >&2 || true

if [ -n "$PREV_TAG" ] && [ "$PREV_TAG" != "$TAG" ]; then
  echo "回滚到 ${PREV_TAG}" >&2
  echo "IMAGE_TAG=${PREV_TAG}" > .release.env
  "${COMPOSE[@]}" up -d --remove-orphans || true
fi
exit 1
