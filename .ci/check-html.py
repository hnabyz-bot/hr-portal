#!/usr/bin/env python3
"""HTML 구조 검사.

- 태그가 제대로 닫혔는지
- id 가 중복되지 않는지

외부 라이브러리를 쓰지 않는다. 파이썬 표준 기능만 사용한다.
"""
import sys
import pathlib
from html.parser import HTMLParser

# 닫는 태그가 없는 태그들
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# HTML5에서 닫는 태그를 생략해도 되는 태그들
OPTIONAL_END = {
    "li", "p", "td", "th", "tr", "tbody", "thead", "tfoot",
    "option", "dt", "dd", "colgroup",
}


class Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
        self.ids = {}

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]

        for name, value in attrs:
            if name == "id" and value:
                if value in self.ids:
                    self.errors.append(
                        f"{line}행: id 가 중복되었습니다 — '{value}' "
                        f"({self.ids[value]}행에서 이미 사용)"
                    )
                else:
                    self.ids[value] = line

        if tag in VOID:
            return

        # <li><li> 처럼 생략된 닫는 태그 처리
        while self.stack and tag in OPTIONAL_END and self.stack[-1][0] == tag:
            self.stack.pop()

        self.stack.append((tag, line))

    def handle_endtag(self, tag):
        line = self.getpos()[0]

        if tag in VOID:
            return

        if not self.stack:
            self.errors.append(f"{line}행: 여는 태그 없이 </{tag}> 가 나왔습니다")
            return

        if self.stack[-1][0] == tag:
            self.stack.pop()
            return

        for idx in range(len(self.stack) - 1, -1, -1):
            if self.stack[idx][0] == tag:
                for unclosed_tag, unclosed_line in self.stack[idx + 1:]:
                    if unclosed_tag not in OPTIONAL_END:
                        self.errors.append(
                            f"{unclosed_line}행: <{unclosed_tag}> 태그가 닫히지 않았습니다"
                        )
                del self.stack[idx:]
                return

        self.errors.append(f"{line}행: </{tag}> 에 짝이 되는 여는 태그가 없습니다")

    def finish(self):
        for tag, line in self.stack:
            if tag not in OPTIONAL_END:
                self.errors.append(f"{line}행: <{tag}> 태그가 닫히지 않았습니다")
        return self.errors


def check(path):
    parser = Checker()
    parser.feed(path.read_text(encoding="utf-8"))
    errors = parser.finish()

    if errors:
        print(f"[실패] {path}")
        for e in errors:
            print(f"   - {e}")
        return False

    print(f"[통과] {path}")
    return True


def main():
    targets = sorted(pathlib.Path("public").rglob("*.html"))

    if not targets:
        print("검사할 HTML 파일이 없습니다.")
        return 0

    ok = True
    for path in targets:
        if not check(path):
            ok = False

    if not ok:
        print()
        print("HTML 구조에 문제가 있습니다. 위에 표시된 행을 확인하세요.")
        print("대부분 태그를 지우다가 짝을 하나 빠뜨린 경우입니다.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
