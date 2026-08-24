#!/usr/bin/env bash
# 개인정보로 보이는 값이 들어갔는지 검사한다.
#
# 현재는 로그인·권한 관리가 없는 상태라 실제 직원 정보를 넣으면 안 된다.
# 화면 확인용 가상 데이터는 .ci/allowed-demo-data.txt 에 등록해 두면 통과한다.
set -uo pipefail

ALLOW=".ci/allowed-demo-data.txt"
TARGETS="public docker-compose.yml backend"
FOUND=0

scan() {
  local name="$1"
  local pattern="$2"
  local hits

  hits=$(grep -rInoE "$pattern" $TARGETS 2>/dev/null || true)
  [ -z "$hits" ] && return

  local remaining=""
  while IFS= read -r line; do
    local value="${line##*:}"
    if [ -f "$ALLOW" ] && grep -qxF "$value" "$ALLOW" 2>/dev/null; then
      continue
    fi
    remaining="${remaining}${line}"$'\n'
  done <<< "$hits"

  if [ -n "${remaining//[$'\n']/}" ]; then
    echo "[실패] $name 형식의 값이 있습니다"
    printf '%s' "$remaining" | sed 's/^/   /'
    echo
    FOUND=1
  fi
}

scan "주민등록번호" '[0-9]{6}-[1-4][0-9]{6}'
scan "휴대전화번호" '01[016789]-?[0-9]{3,4}-?[0-9]{4}'

if [ "$FOUND" -ne 0 ]; then
  cat <<'MSG'
개인정보로 보이는 값이 발견되었습니다.

현재 이 사이트는 로그인이 없어 주소를 아는 사람은 누구나 볼 수 있습니다.
실제 직원의 정보를 넣으면 안 됩니다.

처리 방법은 두 가지입니다.

 1. 실제 정보라면 → 가상의 값으로 바꿔주세요
 2. 화면 확인용 가상 값이라면 → .ci/allowed-demo-data.txt 에 그 값을 한 줄 추가하세요
MSG
  exit 1
fi

echo "[통과] 개인정보 형식 값 없음"
