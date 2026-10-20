-- 하이웍스 메일 쓰기에 받는 사람을 채우는 번호 (2026-10-20 인사담당자 — 조직도의 메일 단추).
-- 서버 DB 는 008 까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py).
-- 하이웍스 메일은 "회사 안 직원 번호"(&user_nos=)로만 받는 사람을 채운다. 로그인·명단에 쓰는 통합 사용자 번호(hiworks_user_no)와 다르고,
-- 공개 API(명단 동기화)로는 받을 수 없다 — HR 이 하이웍스에 로그인한 브라우저에서 짝(통합 번호 → 회사 번호)을 가져와 넣는다.
-- 명단 동기화는 이 칸을 건드리지 않는다. 비어 있는 사람(새 직원 등)은 화면이 메일 주소를 복사해 준다.
ALTER TABLE users ADD COLUMN hiworks_mail_no TEXT NULL
    CHECK (hiworks_mail_no IS NULL OR (hiworks_mail_no GLOB '[1-9]*' AND hiworks_mail_no NOT GLOB '*[^0-9]*' AND length(hiworks_mail_no) <= 12));
