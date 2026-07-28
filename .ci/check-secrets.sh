#!/usr/bin/env bash
# 비밀번호·인증키가 파일에 들어갔는지 검사한다.
#
# 한 번 올라간 비밀번호는 나중에 지워도 변경 이력에 영구히 남는다.
# 그래서 올라가기 전에 막는다.
set -uo pipefail

TARGETS="public docker-compose.yml"
FOUND=0

check() {
  local name="$1"
  local pattern="$2"
  local hits

  hits=$(grep -rInE "$pattern" $TARGETS 2>/dev/null || true)

  if [ -n "$hits" ]; then
    echo "[실패] $name 로 보이는 내용이 있습니다"
    echo "$hits" | sed 's/^/   /'
    echo
    FOUND=1
  fi
}

check "개인키 파일 내용"   '-----BEGIN[A-Z ]*PRIVATE KEY-----'
check "GitHub 토큰"        'gh[pousr]_[A-Za-z0-9]{20,}'
check "AWS 액세스 키"      'AKIA[0-9A-Z]{16}'
check "비밀번호 지정"      '(password|passwd|pwd)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{4,}'
check "API 키 지정"        '(api[_-]?key|secret[_-]?key|access[_-]?token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{8,}'

if [ "$FOUND" -ne 0 ]; then
  cat <<'MSG'
비밀번호나 인증키로 보이는 내용이 발견되었습니다.

이런 값은 파일에 직접 적으면 안 됩니다.
한 번 올라가면 나중에 지워도 변경 이력에 영구히 남기 때문입니다.

해당 부분을 지우고 다시 올려주세요.
어떻게 처리해야 할지 모르겠으면 담당자에게 문의하세요.
MSG
  exit 1
fi

echo "[통과] 비밀번호·인증키 없음"
