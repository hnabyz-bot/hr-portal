#!/usr/bin/env bash
# 한글이 깨지지 않았는지(UTF-8 인코딩) 검사한다.
#
# 편집기 설정에 따라 한글이 ???? 로 저장되는 일이 있다.
set -uo pipefail

BAD=""

while IFS= read -r f; do
  if ! iconv -f UTF-8 -t UTF-8 "$f" >/dev/null 2>&1; then
    BAD="${BAD}${f}"$'\n'
  fi
done < <(find . -path ./.git -prune -o -type f \
  \( -name "*.html" -o -name "*.md" -o -name "*.yml" -o -name "*.yaml" \
     -o -name "*.js" -o -name "*.css" -o -name "*.txt" -o -name "*.sh" \) -print)

if [ -n "${BAD//[$'\n']/}" ]; then
  echo "[실패] 글자가 깨진 파일이 있습니다"
  printf '%s' "$BAD" | sed 's/^/   /'
  echo
  cat <<'MSG'
파일이 UTF-8 형식으로 저장되지 않았습니다.
브라우저에서 한글이 ???? 또는 알 수 없는 기호로 보이게 됩니다.

GitHub 웹 화면에서 편집하면 이 문제가 생기지 않습니다.
다른 편집기를 쓰셨다면 저장할 때 인코딩을 UTF-8 로 지정하세요.
MSG
  exit 1
fi

echo "[통과] 인코딩 정상 (UTF-8)"
