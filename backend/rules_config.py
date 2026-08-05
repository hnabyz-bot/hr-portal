"""
규정 검색 대상 목록.

실제 규정 원문 파일(PDF/DOCX)은 저장소에 올리지 않는다 — 서버의
REGULATIONS_DIR 안에 아래 filename과 정확히 같은 이름으로 직접 넣어야 한다
(관리자가 SSH로 직접 업로드, git으로 배포되지 않음).

여기 있는 title만 검색 결과에 노출되며, filename은 서버에서 파일을 찾는
용도로만 쓰인다.
"""

import os

REGULATIONS_DIR = os.environ.get("REGULATIONS_DIR", "/app/regulations")

RULES = [
    {"id": "hr", "title": "인사규정", "filename": "1. 인사규정(2026.05).pdf"},
    {"id": "eval", "title": "인사평가규정", "filename": "2. 인사평가규정(2025.03).docx"},
    {"id": "service", "title": "복무관리규정", "filename": "3. 복무관리규정(2025.01).docx"},
    {"id": "pay", "title": "급여규정", "filename": "4. 급여규정(2025.01).docx"},
    {"id": "welfare", "title": "직원복리후생규정", "filename": "5. 직원복리후생규정(2026.07).pdf"},
    {"id": "travel", "title": "출장여비규정", "filename": "6. 출장여비규정(2026.07).pdf"},
    {"id": "discipline", "title": "표창징계규정(상벌규정)", "filename": "7. 표창징계규정(상벌규정)(2026.05).pdf"},
    {"id": "security", "title": "정보보안 규정", "filename": "8. 정보보안_규정(2026.05).pdf"},
    {"id": "nondiscrimination", "title": "차별금지 규정", "filename": "9. 차별금지 규정(2026.05).pdf"},
    {"id": "grievance", "title": "고충처리위원회 운영 규정", "filename": "10. 고충처리위원회 운영 규정(2026.05).pdf"},
    {"id": "anticorruption", "title": "반부패 뇌물정책", "filename": "반부패 뇌물정책(26.01).pdf"},
]


def get_rule(rule_id: str) -> dict | None:
    return next((r for r in RULES if r["id"] == rule_id), None)


def rule_file_path(rule: dict) -> str:
    return os.path.join(REGULATIONS_DIR, rule["filename"])
