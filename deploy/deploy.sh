#!/usr/bin/env bash
# docmill 원격 모드 배포 스크립트 (FR-24)
#
# 새 이미지를 받아 컨테이너를 바꾸고, /healthz가 시간 안에 200이 되지 않으면 이전 이미지로 되돌린다.
#
# 사용법:
#   DOCMILL_HTTP_TOKEN=... bash deploy/deploy.sh --image <새 이미지> [--previous <이전 이미지>] \
#       [--timeout <초>] [--port <호스트 포트>] [--name <컨테이너 이름>] [--volume <데이터 볼륨>]
#
# --previous를 주지 않으면 지금 실행 중인 컨테이너의 이미지 ID(태그가 아닌 고정 ID)를 이전 이미지로 쓴다.
# DOCMILL_HTTP_TOKEN이 없거나 공백뿐이면 docker를 호출하기 전에 끝나 기존 컨테이너를 건드리지 않는다.
# 종료 코드: 0 배포 성공, 1 헬스 체크 실패(되돌림 시도함), 2 사용법 오류, 3 DOCMILL_HTTP_TOKEN 없음.
set -euo pipefail

image=""
previous=""
timeout=60
port=8000
name="docmill"
volume="docmill-data"

usage() {
  sed -n '6,8p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --image|--previous|--timeout|--port|--name|--volume)
      [ $# -ge 2 ] || { echo "$1에 값이 필요합니다" >&2; usage; } ;;
  esac
  case "$1" in
    --image) image="${2:-}"; shift 2 ;;
    --previous) previous="${2:-}"; shift 2 ;;
    --timeout) timeout="${2:-}"; shift 2 ;;
    --port) port="${2:-}"; shift 2 ;;
    --name) name="${2:-}"; shift 2 ;;
    --volume) volume="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "알 수 없는 인자: $1" >&2; usage ;;
  esac
done

[ -n "$image" ] || { echo "--image가 필요합니다" >&2; usage; }
case "$timeout" in ''|*[!0-9]*) echo "--timeout은 초 단위 정수여야 합니다" >&2; exit 2 ;; esac
case "$port" in ''|*[!0-9]*) echo "--port는 정수여야 합니다" >&2; exit 2 ;; esac

# Must run before any docker call so a missing token never touches the current container.
token_trimmed="${DOCMILL_HTTP_TOKEN:-}"
token_trimmed="${token_trimmed//[[:space:]]/}"
if [ -z "$token_trimmed" ]; then
  echo "DOCMILL_HTTP_TOKEN이 없거나 공백뿐입니다. 토큰이 없으면 컨테이너가 127.0.0.1에만 바인딩해 외부에서 접속할 수 없으므로 배포하지 않습니다" >&2
  exit 3
fi
unset token_trimmed

health_url="http://127.0.0.1:${port}/healthz"

if [ -z "$previous" ]; then
  # Pin the image ID (not the mutable tag) before `docker pull` can move the tag.
  previous="$(docker inspect --format='{{.Image}}' "$name" 2>/dev/null || true)"
fi

# Options use the `--flag=value` form so the image is the first non-option argument.
start() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d \
    --name="$name" \
    --restart=unless-stopped \
    --publish="${port}:8000" \
    --volume="${volume}:/data" \
    --env=DOCMILL_HTTP_TOKEN \
    "$1"
}

healthy() {
  local deadline=$((SECONDS + timeout))
  while :; do
    if curl -fsS --max-time 5 -o /dev/null "$health_url"; then
      return 0
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      return 1
    fi
    sleep 1
  done
}

echo "새 이미지 받기: $image" >&2
docker pull "$image"

echo "새 이미지 실행: $image" >&2
if start "$image" && healthy; then
  echo "배포 성공: $image" >&2
  exit 0
fi

echo "헬스 체크 실패: ${timeout}초 안에 $health_url 이 200을 돌려주지 않았습니다" >&2
docker logs --tail=50 "$name" >&2 || true

if [ -z "$previous" ]; then
  echo "되돌릴 이전 이미지가 없습니다" >&2
  exit 1
fi

echo "이전 이미지로 되돌리기: $previous" >&2
if start "$previous" && healthy; then
  echo "되돌림 완료: $previous" >&2
else
  echo "되돌린 이미지도 헬스 체크를 통과하지 못했습니다: $previous" >&2
fi
exit 1
