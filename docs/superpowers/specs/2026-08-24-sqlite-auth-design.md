# SQLite 전환 + 로그인·권한 설계

- 작성일: 2026-08-24
- 앞 문서: [2026-08-24-hr-evaluation-contract-design.md](2026-08-24-hr-evaluation-contract-design.md) (평가·계약 업무 규칙 — 그대로 유효)
- 상태: 설계 검토 대기

## 1. 왜 바꾸나

앞 문서는 **자체 호스팅 PostgreSQL + 직접 만든 인증**을 전제로 썼다. 그 전제가 두 가지 사실 때문에 바뀌었다.

1. **전담 서버 관리자가 없다.** 저장소 소유자(비전공자)가 주로 관리하고, 개발본부장이 질문·검토를 받아준다.
2. **개발본부장이 SQLite를 권했다.**

관리자 없는 서버에서 PostgreSQL을 운영하면 백업을 아무도 검증하지 않는 상태가 된다. SQLite는 데이터베이스가 파일 하나라 **백업이 파일 복사**이고, 비전공자가 눈으로 확인할 수 있다. 이 프로젝트 규모(직원 수백 명, 평가철에 몰리는 사용)에서 SQLite는 성능상 여유롭다.

외부 클라우드(Supabase 등)는 검토했으나 채택하지 않는다. 인사데이터가 사외로 나가면 개인정보처리방침에 수탁자 공개가 필요하고, README §7이 이미 "개인정보 국외이전 검토"를 미결로 올려둔 상태다. 관리자 부재 문제는 SQLite의 쉬운 백업으로 푸는 쪽을 택한다.

**앞 문서의 업무 규칙(쿼터·S등급·KPI·계약 잠금)은 하나도 바뀌지 않는다.** 바뀌는 건 저장 방식과, 새로 붙는 인증·권한 계층이다.

## 2. 이번 작업의 범위

**만드는 것**

1. `001_init.sql`을 SQLite용으로 교체
2. 저장소 구현체 (`ports.py`의 Protocol을 sqlite3로 구현)
3. 인증: 로그인·로그아웃·비밀번호 변경, 세션, 로그인 실패 잠금
4. 권한: 조회 권한 판정 함수 + Flask 데코레이터
5. HR 계정 관리: 계정 발급·비밀번호 초기화·비활성화
6. 백업 자동화 스크립트 + 서버 cron 등록
7. CI: PostgreSQL 서비스 컨테이너 제거, SQLite 스키마 검사로 교체

**만들지 않는 것 (다음 PR)**

- 평가·성과 / 급여·계약 **화면** (`public/index.html`)
- 조회 API 엔드포인트 (권한 판정 함수는 만들지만, 그걸 쓰는 화면용 API는 화면과 함께)
- PDF 렌더링
- 2단계 인증 (README §7 항목, 외부 접속 정책 정해지면)
- SSO 연계 (그룹웨어가 외부 앱 SSO를 열어주는지 미확인)

## 3. 확정된 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 데이터 저장 | **SQLite** (`backend/data/hr.db`) | 개발본부장 권고, 백업 용이성 |
| 로그인 | **직접 구현** | 새 라이브러리 0개로 가능 |
| 계정 아이디 | **회사 메일 주소** | 사용자 결정. `users.email`이 이미 UNIQUE |
| 자가가입 | **없음** | HR_ADMIN이 발급 |
| 비밀번호 찾기 | **없음** | HR_ADMIN이 초기화. 메일 발송 불필요 |
| 비밀번호 해시 | `werkzeug.security` (scrypt) | Flask에 이미 포함. 직접 짜지 않는다 |
| 세션 | 서버 저장 세션 + 쿠키 | 강제 로그아웃이 가능해야 함 |
| 월 비용 | 0원 | |

### 새 의존성 없음

`backend/requirements.txt`를 건드리지 않는다. 필요한 건 전부 이미 있다.

| 필요한 것 | 어디서 |
|---|---|
| SQLite | 파이썬 표준 라이브러리 `sqlite3` |
| 비밀번호 해시 | `werkzeug.security` (Flask가 이미 의존) |
| 세션 토큰 생성 | 파이썬 표준 라이브러리 `secrets` |
| HTTPS | Cloudflare가 이미 처리 |

## 4. 권한 표

**굵은 칸이 이번에 확정된 것이다.**

| 무엇을 | 팀원 | 팀장 | 본부장 | 인사담당자 |
|---|:---:|:---:|:---:|:---:|
| 내 연봉계약서 조회·서명 | ○ | ○ | ○ | ○ |
| **남의 연봉계약서 조회** | ✗ | **✗** | **자기 본부 전원** | 전사 |
| 계약 파기·재발송 | ✗ | ✗ | ✗ | ○ |
| 내 KPI 등록·수정요청 | ○ | ○ | ○ | ○ |
| 남의 KPI 조회 | ✗ | 자기 팀 | 자기 본부 | 전사 |
| KPI 1차 승인 | ✗ | 자기 팀 | — | ○ |
| KPI 2차 승인(확정) | ✗ | ✗ | 자기 본부 | ○ |
| 내 평가 결과 조회 | ○ | ○ | ○ | ○ |
| 남의 평가 점수·등급 | ✗ | 자기 팀 | 자기 본부 | 전사 |
| 등급 배정·제출 | ✗ | ✗ | 자기 본부 | ○ |
| 본부 쿼터 조율 | ✗ | ✗ | ✗ | ○ |
| 계정 발급·비밀번호 초기화 | ✗ | ✗ | ✗ | ○ |
| 감사 로그 조회 | ✗ | ✗ | ✗ | ○ |

**팀장은 팀원의 연봉계약서를 볼 수 없다.** 연봉은 조직장에게도 비공개이며, 본부장만 예외다.

**대리 서명은 누구도 할 수 없다.** 인사담당자도 남의 계약서에 서명하지 못한다. 이미 구현돼 있다.

**본부장이 보는 범위는 자기 본부 소속 전원이다** (본부 직속 + 하위 팀 전원). 전사가 아니다. — 2026-08-24 사용자 확정.

"자기 본부"는 `departments.leader_user_id = 본인` 이면서 `level = 'DIVISION'`인 부서를 말한다. 본부에 **소속된** 것과 본부의 **장인** 것은 다르므로, 소속만으로 판정하지 않는다.

### 가정 (반대 지시가 없으면 이대로)

- 본부장이 공석인 본부는 HR_ADMIN이 대행한다.
- 한 사람은 역할 하나만 갖는다. 겸직(팀장이면서 본부장)은 없다.

## 5. PostgreSQL → SQLite 전환 규칙

| PostgreSQL | SQLite | 비고 |
|---|---|---|
| `CREATE TYPE ... AS ENUM` | `TEXT` + `CHECK (col IN (...))` | 값은 파이썬 Enum과 동일. selfcheck가 대조 |
| `BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY` | `INTEGER PRIMARY KEY AUTOINCREMENT` | |
| `TIMESTAMPTZ` | `TEXT` — ISO 8601 UTC (`2027-01-01T09:00:00Z`) | 문자열 정렬이 곧 시간 정렬 |
| `DATE` | `TEXT` — `YYYY-MM-DD` | |
| `BOOLEAN` | `INTEGER` 0/1 + `CHECK (col IN (0,1))` | |
| `NUMERIC(14,0)` 연봉 | `INTEGER` — 원 단위 | 정확 |
| `NUMERIC(6,2)` 점수·가중치·인상률 | `INTEGER` — **100배 저장** | 아래 참고 |
| `JSONB` | `TEXT` — JSON 문자열 | |
| `BYTEA` | `BLOB` | 서명 이미지 |
| `INET` | `TEXT` | |
| `GENERATED ALWAYS AS (...) STORED` | 동일하게 지원됨 (SQLite 3.31+) | 확인함 |
| 부분 유니크 인덱스 | 동일하게 지원됨 | 확인함 |
| plpgsql 트리거 함수 | SQLite 트리거 + `RAISE(ABORT, '...')` | 확인함 |
| `SELECT ... FOR UPDATE` | `BEGIN IMMEDIATE` 트랜잭션 | 잠금 단위가 DB 전체. 이 규모에선 무방 |

### 소수를 100배 정수로 저장하는 이유

SQLite에는 정확한 소수 자료형이 없다. `REAL`은 부동소수점이라 소수 2자리 가중치들의 합이 정확히 `100.00`이 되지 않는 경우가 있다. **가중치 합계 100 검증이 이 오차로 깨진다.**

> **2026-08-26 정정.** 이 문서는 원래 `33.33 + 33.33 + 33.34`를 예시로 들었으나, 검증 결과 **이 조합은 IEEE754 배정밀도에서 정확히 `100.0`이 되어 오차가 재현되지 않는다.** 결론(REAL은 위험하다)은 그대로지만 근거 예시가 틀렸으므로 실제로 깨지는 조합으로 교체한다.
>
> ```
> 7.64 + 83.57 + 8.79 → 99.99999999999999   (합계 100.00 이어야 하는데 아님)
> 764  + 8357  + 879  → 10000               (100배 정수는 정확)
> ```
>
> 무작위 탐색으로 3~10개 항목 조합에서 반례가 다수 확인되었다. 항목 수가 늘수록 오차가 날 확률이 높아진다. 검증 코드: `backend/hr_eval/sql/check_sqlite_features.py`

그래서 소수 2자리 값은 100배 정수로 저장한다.

| 도메인 값 | DB 저장값 | 칼럼명 |
|---|---|---|
| `Decimal("33.33")` | `3333` | `weight_pct_x100` |
| `Decimal("100.00")` | `10000` | |
| `Decimal("110.00")` | `11000` | `total_score_x100` |
| `Decimal("7.77")` (인상률) | `777` | `raise_pct_x100` |

**칼럼 이름에 `_x100`을 붙여** DB 파일을 직접 열어본 사람이 헷갈리지 않게 한다.

변환은 **저장소 계층에서만** 한다. 도메인 로직은 계속 `Decimal`을 쓰고, 44개 검증도 그대로다.

### 함정: 외래키가 기본으로 꺼져 있다

**SQLite는 `PRAGMA foreign_keys`가 기본값 0이다.** 켜지 않으면 외래키 제약이 선언만 되고 실제로는 아무것도 막지 않는다. 존재하지 않는 부서에 직원을 넣어도 조용히 저장된다.

**연결을 열 때마다 켜야 한다.** 연결마다 따로 설정되므로 한 번 켜두면 되는 게 아니다.

```python
conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA foreign_keys = ON")     # 반드시. 빠뜨리면 외래키가 무력화된다
conn.execute("PRAGMA journal_mode = WAL")    # 읽기가 쓰기를 막지 않게
conn.execute("PRAGMA busy_timeout = 5000")   # 쓰기 충돌 시 5초 대기 후 포기
```

이 세 줄이 빠지면 안 되므로, 연결을 만드는 함수 하나로 강제하고 selfcheck가 확인한다.

> **2026-08-26 추가 — 더 고약한 함정: `PRAGMA`는 트랜잭션 안에서 조용히 무시된다.**
>
> 검증 중 발견했다. 연결 직후가 아니라 **DML을 한 번이라도 실행한 뒤**에 `PRAGMA foreign_keys = ON`을 걸면, 파이썬 `sqlite3`가 이미 암시적 트랜잭션을 열어둔 상태라 **설정이 적용되지 않는다. 오류도 나지 않는다.**
>
> ```python
> conn.execute("INSERT INTO users ...")      # 암시적 트랜잭션 시작
> conn.execute("PRAGMA foreign_keys = ON")   # 조용히 무시됨
> conn.execute("PRAGMA foreign_keys").fetchone()   # → (0,)  여전히 꺼져 있다
> ```
>
> 따라서 **연결을 만드는 함수에서, 어떤 쿼리보다도 먼저** 걸어야 한다. 이것이 "연결 함수 하나로 강제한다"가 권장이 아니라 필수인 이유다. `check_sqlite_features.py`가 이 동작을 CI에서 재현한다.

## 6. 새 테이블

앞 문서의 테이블 11개는 위 전환 규칙에 따라 형태만 바뀌고 제약조건은 그대로 유지한다. 여기에 인증용 테이블이 더해진다.

### 6.1 `users` 확장

기존 칼럼(`employee_no`, `name`, `email`, `role`, `department_id`, `hire_date`, `is_active`)은 그대로 두고 다음을 추가한다.

```sql
    password_hash        TEXT    NULL,     -- werkzeug scrypt 해시. NULL이면 아직 미발급
    password_set_at      TEXT    NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1 CHECK (must_change_password IN (0,1)),
    last_login_at        TEXT    NULL,
    deactivated_at       TEXT    NULL,
```

`email`은 이미 `UNIQUE`라 그대로 로그인 아이디로 쓴다.

`must_change_password`가 1이면 로그인은 되지만 비밀번호 변경 화면 외의 모든 페이지가 막힌다. HR이 발급한 임시 비밀번호를 계속 쓰지 못하게 한다.

### 6.2 `sessions`

```sql
CREATE TABLE sessions (
    token_hash   TEXT    PRIMARY KEY,   -- 토큰 원본의 SHA-256. 원본은 저장하지 않는다
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT    NOT NULL,
    last_seen_at TEXT    NOT NULL,
    expires_at   TEXT    NOT NULL,
    ip           TEXT    NULL,
    user_agent   TEXT    NULL,
    revoked_at   TEXT    NULL
);
CREATE INDEX idx_sessions_user ON sessions(user_id) WHERE revoked_at IS NULL;
```

**토큰 원본을 저장하지 않는다.** DB 파일이 유출돼도 그 안의 값으로 로그인할 수 없다. 비밀번호를 해시로 저장하는 것과 같은 이유다.

세션 유효기간은 12시간, 활동이 있으면 갱신한다. 인사 정보를 다루므로 길게 두지 않는다.

### 6.3 `login_attempts`

```sql
CREATE TABLE login_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT    NOT NULL,
    ip           TEXT    NULL,
    attempted_at TEXT    NOT NULL,
    succeeded    INTEGER NOT NULL CHECK (succeeded IN (0,1))
);
CREATE INDEX idx_login_attempts_email ON login_attempts(email, attempted_at DESC);
CREATE INDEX idx_login_attempts_ip    ON login_attempts(ip, attempted_at DESC);
```

**같은 이메일로 10분 내 5회 실패하면 10분간 잠근다.** 같은 IP로 10분 내 20회 실패해도 잠근다(여러 계정을 훑는 공격 대비).

기록은 90일 후 지운다. 로그인 시각·IP도 개인정보다.

## 7. 로그인 흐름

```
[HR] 계정 발급 ─ 이메일·이름·사번·소속·역할 + 임시 비밀번호
                 must_change_password = 1

[직원] 로그인 ─ 이메일 + 비밀번호
        │
        ├─ 잠금 확인 (login_attempts)
        ├─ users 조회 → is_active 확인
        ├─ check_password_hash() 검증
        ├─ 실패 → login_attempts 기록, "이메일 또는 비밀번호가 올바르지 않습니다"
        └─ 성공 → 세션 토큰 생성 → 쿠키 발급
                  must_change_password=1 이면 비밀번호 변경 화면으로 강제 이동

[요청마다] 쿠키 → 세션 조회 → 만료·폐기 확인 → Actor(user_id, role) 생성
                                                └─ 이미 만든 유스케이스에 그대로 전달
```

**로그인 실패 메시지는 하나로 통일한다.** "없는 계정입니다"와 "비밀번호가 틀렸습니다"를 구분해서 보여주면, 공격자가 어떤 이메일이 실재하는지 알아낼 수 있다.

### 이미 만든 코드와 어떻게 붙나

유스케이스는 이미 `Actor(user_id, role)`을 인자로 받게 설계돼 있다.

```python
def submit_kpi_goal(*, actor: Actor, period_id: int, ...) -> KpiSheet:
    if actor.user_id != user_id and actor.role is not Role.HR_ADMIN:
        raise PermissionDeniedError("본인 또는 인사담당자만 KPI를 제출할 수 있습니다")
```

**인증 계층이 `Actor`를 만들어 넘기면 끝이다. 도메인 로직은 한 줄도 안 고친다.**

### 쿠키 설정

```python
response.set_cookie(
    "hr_session", token,
    max_age=12 * 3600,
    secure=True,        # HTTPS로만 전송. Cloudflare가 TLS 종단
    httponly=True,      # 자바스크립트가 못 읽음 (XSS로 탈취 방지)
    samesite="Lax",     # 다른 사이트에서 온 요청에 쿠키를 안 실음 (CSRF 완화)
)
```

**세 플래그 모두 필수다.** 하나라도 빠지면 세션 탈취 경로가 열린다.

`SECRET_KEY`는 서버 `.env`에만 둔다. 저장소에 올리지 않고, 서버 재시작 때마다 바뀌면 전원이 로그아웃되므로 고정값이어야 한다.

## 8. 조회 권한 판정

`backend/hr_eval/domain/access.py` — 순수 함수. DB도 Flask도 import하지 않는다.

```python
@dataclass(frozen=True)
class Viewer:
    """권한 판정에 필요한 최소 정보. 인증 계층이 채워서 넘긴다."""
    user_id: int
    role: Role
    team_id: int | None        # 소속 팀 (본부장은 None일 수 있음)
    division_id: int | None    # 소속 본부
    led_division_id: int | None  # 본인이 장인 본부 (본부장만)
    led_team_id: int | None      # 본인이 장인 팀 (팀장만)


@dataclass(frozen=True)
class Target:
    user_id: int
    team_id: int | None
    division_id: int | None


def can_view_contract(viewer: Viewer, target: Target) -> bool:
    """연봉계약서 조회. 팀장은 본인 것만 본다."""
    if viewer.user_id == target.user_id:
        return True
    if viewer.role is Role.HR_ADMIN:
        return True
    if viewer.role is Role.DIVISION_HEAD:
        return (
            viewer.led_division_id is not None
            and viewer.led_division_id == target.division_id
        )
    return False


def can_view_evaluation(viewer: Viewer, target: Target) -> bool:
    """평가 점수·등급 조회. 팀장은 자기 팀까지 본다."""
    if viewer.user_id == target.user_id:
        return True
    if viewer.role is Role.HR_ADMIN:
        return True
    if viewer.role is Role.DIVISION_HEAD:
        return (
            viewer.led_division_id is not None
            and viewer.led_division_id == target.division_id
        )
    if viewer.role is Role.TEAM_LEADER:
        return viewer.led_team_id is not None and viewer.led_team_id == target.team_id
    return False
```

`can_view_kpi`는 `can_view_evaluation`과 같은 규칙이다.

**`led_division_id`가 핵심이다.** 본부 소속인 것과 본부장인 것은 다르다. 본부 소속 팀원이 본부장 권한을 갖지 않도록, "장"인지를 별도 필드로 판정한다.

### 세 겹으로 막는다

| 겹 | 무엇 | 막히면 |
|---|---|---|
| 화면 | 권한 없는 메뉴·버튼을 안 그림 | 실수로 누르는 걸 막음 |
| **서버** | 모든 조회가 위 함수를 통과 | **주소를 직접 입력해도 막힘 — 진짜 방어선** |
| DB | 기존 제약조건·트리거 | 앱에 버그가 있어도 데이터가 안 깨짐 |

**화면에서 숨기는 것은 보안이 아니다.** 주소창에 직접 입력하면 뚫린다. 서버 검사가 없으면 권한 설정이 있으나 마나다.

목록 조회는 판정 함수를 행마다 부르지 않고 **SQL 조건으로 바꿔서** 애초에 남의 행을 읽지 않게 한다. 판정 함수와 SQL 조건이 어긋나지 않도록, selfcheck가 두 경로의 결과를 대조한다.

## 9. 백업

**이 설계에서 유일하게 사람 손이 필요한 부분이다.**

- 매일 03:00, `sqlite3 hr.db ".backup"`으로 백업 (WAL 모드에서도 안전한 온라인 백업)
- 파일명에 날짜를 붙여 30일치 보관, 그보다 오래된 것은 삭제
- 백업 파일 권한 600, DB 파일도 600
- 스크립트는 `backend/scripts/backup_db.sh`, 서버 cron에 등록

**저장소 소유자가 할 일: 가끔 백업 폴더에 파일이 쌓이는지 눈으로 확인한다.** 백업이 조용히 멈춰 있는 것이 백업 사고의 대부분이다. 전공 지식 없이 파일 날짜만 보면 된다.

월 1회, 백업 파일을 열어 직원 수를 세어보는 복구 확인을 권한다. 명령 한 줄로 되게 스크립트를 같이 만든다.

## 10. CI 변경

`schema` job에서 **PostgreSQL 서비스 컨테이너를 제거한다.** SQLite는 파이썬에 내장돼 있어 서비스가 필요 없다.

```yaml
  schema:
    name: DB 스키마 검사
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: 스키마 적용 + 제약조건 확인
        working-directory: backend
        run: python -m hr_eval.sql.check_schema
```

기존 `checks.sql`의 제약조건 검사 8종은 파이썬 스크립트로 옮긴다. `sqlite3`로 임시 DB를 만들어 스키마를 적용하고, 잘못된 데이터가 실제로 거부되는지 확인한다. **CI가 더 빨라지고 단순해진다.**

`promote` job의 `needs`는 그대로 `[validate, smoke, domain, schema]`를 유지한다.

## 11. 개발본부장 검토 요청 항목

구현 후 이 여섯 가지를 봐주시기를 요청한다. 직접 만든 인증에서 사고가 나는 지점이다.

1. **`SECRET_KEY` 관리** — 서버 `.env`에만 존재하는가, 고정값인가, 저장소에 없는가
2. **쿠키 플래그** — `Secure` / `HttpOnly` / `SameSite` 세 개가 모두 붙어 있는가
3. **로그인 실패 잠금** — 임계치와 잠금 시간이 적절한가, 실패 메시지가 계정 존재 여부를 노출하지 않는가
4. **세션 토큰 해시 저장** — DB에 원본이 남지 않는가, 만료·강제 로그아웃이 동작하는가
5. **`PRAGMA foreign_keys = ON`** — 모든 연결 경로에서 켜지는가
6. **백업** — cron이 실제로 도는가, 복구가 되는가, 파일 권한이 600인가

## 12. 남은 결정 사항

- **2단계 인증** — README §7이 외부 접속 시 요구한다. 사외 접속 정책이 정해지면 TOTP 방식으로 추가한다.
- **SSO 연계** — 그룹웨어가 외부 앱 SSO를 열어주는지 미확인. 가능해지면 붙일 수 있게, 인증 계층과 도메인 계층을 분리해둔다.
- **주민번호** — 급여 모듈에서 필요해지면 암호화 저장이 법적 의무다. 현재 스키마에는 칼럼이 없다. 그때 별도 설계한다.
- **GROUPED/INDIVIDUAL 모드 경계** — 앞 문서 §9에서 이어지는 미결 항목 (5명 본부는 상위가 40%)
- **서명 IP·서명 이미지 보관기간**
