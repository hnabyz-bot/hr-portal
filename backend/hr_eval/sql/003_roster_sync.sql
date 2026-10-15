-- 직원 명단 동기화 (로그인 설계 §11, 3단계).
-- 서버 DB 는 002 까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py).

-- 포털 부서와 하이웍스 조직을 짝짓는다. 부서는 하이웍스 조직도를 따라 만들고 고친다.
-- (ALTER TABLE 로는 UNIQUE 칸을 더할 수 없어 유니크 인덱스로 막는다)
ALTER TABLE departments ADD COLUMN hiworks_node_id TEXT NULL;
CREATE UNIQUE INDEX uq_departments_hiworks ON departments(hiworks_node_id);

-- 연락처. 하이웍스 값으로 고친다 (조직도에 보이는 휴대폰은 로그인 뒤 본인이 공개 여부를 고른다)
ALTER TABLE users ADD COLUMN phone  TEXT NULL;   -- 하이웍스 phone (사무실)
ALTER TABLE users ADD COLUMN mobile TEXT NULL;   -- 하이웍스 cell (휴대폰)
