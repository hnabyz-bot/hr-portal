#!/usr/bin/env bash
# public 폴더에 공개되면 안 되는 파일이 들어갔는지 검사한다.
#
# public 폴더 안의 파일은 인터넷에 그대로 공개된다.
set -uo pipefail

ALLOWED_EXT="html|css|js|png|jpg|jpeg|gif|svg|ico|webp|woff|woff2|ttf|pdf"
FOUND=0

if [ ! -d public ]; then
  echo "public 폴더가 없습니다."
  exit 1
fi

# 허용되지 않은 확장자
BAD=$(find public -type f | grep -vE "\.($ALLOWED_EXT)$" || true)
if [ -n "$BAD" ]; then
  echo "[실패] 공개하면 안 되는 형식의 파일이 있습니다"
  echo "$BAD" | sed 's/^/   /'
  echo
  FOUND=1
fi

# 숨김 파일
HIDDEN=$(find public -name ".*" -not -name "." -not -name ".." || true)
if [ -n "$HIDDEN" ]; then
  echo "[실패] 숨김 파일이 있습니다"
  echo "$HIDDEN" | sed 's/^/   /'
  echo
  FOUND=1
fi

# 위험한 이름
RISKY=$(find public -type f \( -iname "*.env*" -o -iname "*.key" -o -iname "*.pem" \
  -o -iname "*.sql" -o -iname "*backup*" -o -iname "*.xlsx" -o -iname "*.docx" \) || true)
if [ -n "$RISKY" ]; then
  echo "[실패] 내부 자료로 보이는 파일이 있습니다"
  echo "$RISKY" | sed 's/^/   /'
  echo
  FOUND=1
fi

if [ "$FOUND" -ne 0 ]; then
  cat <<'MSG'
public 폴더에는 화면을 만드는 데 필요한 파일만 넣어야 합니다.

이 폴더의 파일은 인터넷에 그대로 공개됩니다.
주소만 알면 누구나 내려받을 수 있습니다.

설정 파일, 내부 문서, 직원 명단 같은 것은 public 폴더 밖에 두세요.
MSG
  exit 1
fi

echo "[통과] public 폴더 정상"
