# hr-portal — 작업 컨텍스트

인사 통합포털. 대부분 화면은 아직 PoC 목업(백엔드·인증·DB 없음)이지만, **출장·경비 > 경비 산출기는 실제 백엔드(backend/)가 붙어 있다** (아래 "백엔드가 있는 기능" 참고). 자세한 배경은 [README.md](README.md), 작업 절차는 [docs/beginner-guide.md](docs/beginner-guide.md) 참고.

## 협업 방식

저장소 소유자는 초심자(비개발자). 코드는 내가 작성하고, 판단(무엇을 만들지·규칙)은 저장소 소유자가 정한다.

**[이슈 #11](https://github.com/hnabyz-bot/hr-portal/issues/11) — 비개발자 기여자용 작업 규칙**을 앞으로의 작업 기준으로 따른다 (2026-08-07, 사용자 지시). 요약:

- 기능 하나 = 브랜치 하나 = PR 하나. 여러 기능을 한 PR에 섞지 않는다.
- 브랜치 네이밍: `feature/짧은-설명` 또는 `fix/짧은-설명` 지향 (지금까지는 설명적 이름만 써왔음 — 앞으로는 접두사 붙인다).
- `main`/`release` 직접 push 금지 — 브랜치 보호 규칙이 아직 없어 기술적으로는 가능하지만, 습관적으로 PR을 거친다.
- **merge된 브랜치는 정리(삭제)한다** — 지금까지 안 해서 원격에 브랜치가 쌓여 있었음. merge 직후 바로 삭제하는 걸 새 기본 동작으로 한다.
- PR 본문에는 무엇을/왜/배포 시 주의사항을 명확히 적는다. 백엔드·시크릿이 필요한 기능이면 배포 전 필요한 절차(`.env` 생성, `docker compose up` 등)를 PR 본문에 반드시 포함한다 — 지금까지 해온 방식 그대로 유지.
- **API 키·비밀번호·규정 원문 등 민감 자료를 절대 커밋하지 않는다.** hr-portal은 Public 저장소다. 서버용 시크릿은 저장소 루트의 `.env`(gitignore됨, 서버에 직접 생성)로, 규정 원문은 `regulations/`(마찬가지로 gitignore)로 전달한다. `.env.example`에 필요한 변수 이름만 적어둔다.
- 실제 직원 개인정보를 절대 넣지 않는다. 표시 데이터는 하드코딩된 가상 데이터만 사용.

이슈가 추가로 제안한 것 중 **아직 안 한 것** (내가 임의로 만들지 않음, 필요하면 사용자가 요청): `CONTRIBUTING.md`/`DEPLOY.md` 신설, 이슈 템플릿, CI를 머지 필수 조건으로 강제(브랜치 보호 규칙에 딸림). 브랜치 보호 규칙 자체도 여전히 미설정 상태(이슈 #12).

수정 대상은 대부분 `public/index.html` (단일 파일, 인라인 CSS/JS). `docker-compose.yml`/`nginx.conf`/`backend/`도 경비 산출기·규정검색 백엔드가 생기면서 실제로 쓰이는 파일이다.

## 배포 흐름 (실제로 동작하는 것)

```
브랜치 생성 → 구현 → 로컬 확인 → 커밋/푸시
  → PR 생성 → CI 통과 확인 → merge
  → release 브랜치 자동 갱신 → 서버 타이머(2분) → https://hr.abyz-lab.work 반영
```

- **로컬 확인**: 이 개발 환경엔 docker가 없다(`docker: command not found`). `docker compose up`은 서버에서만 가능. 로컬에선 `python3 -m http.server <port>` 를 `public/` 안에서 띄우고 브라우저로 확인한다 (file:// 직접 열기는 Claude Code 브라우저 프리뷰에서 "static snapshot"으로 렌더링되어 클릭 등 상호작용 확인이 안 됨 — 반드시 http 서버 경유).
- **PR 생성·merge**: `gh` CLI 없음. `git credential fill`로 얻은 GitHub 토큰으로 REST API(`api.github.com/repos/hnabyz-bot/hr-portal/pulls`)를 직접 호출해 PR 생성·merge까지 처리 가능 (사용자 승인 하에). 사용자 자격 증명을 재사용하는 것이므로 매번 실행 전 사용자에게 진행해도 되는지 확인한다.
- **PR별 미리보기(Cloudflare Pages)는 아직 미구성** — beginner-guide 5장이 설명하는 미리보기 링크는 지금 뜨지 않는다.
- **서버 SSH 접근 가능 (2026-08-07부터)**: `ssh hr-portal-server` (별칭, `~/.ssh/config`에 등록됨, 전용 키 `~/.ssh/hr-portal-server`). `docker compose`, 배포 스크립트 수정, 파일 업로드(scp) 등 서버 작업을 직접 할 수 있다. 다만 이건 실제 운영 서버이고 같은 서버에 무관한 서비스(`voc-mr-ecr`)도 같이 떠 있으니, 그쪽은 절대 건드리지 않는다. 시크릿 값은 `cat`/`Read`로 열어보지 않고 scp로만 전달한다.

CI(`.github/workflows/ci.yml`)가 자동 검사하는 것: 한글 인코딩, HTML 구조, 시크릿, 개인정보 패턴, `public/` 노출 범위, 스모크 테스트. 실패하면 원인을 그대로 알려준다.

## 백엔드가 있는 기능 (출장·경비 > 경비 산출기)

`secom-allowance-calculator`(로컬 Streamlit 앱, `http://10.20.6.63:8501` — 건드리지 않음, 그대로 유지)의 로직을 hr-portal에도 포팅한 것. Naver 지도(주소→좌표, 경로), Naver 지역검색(장소명→주소), Opinet(전월 경기도 평균 유가) API를 실시간으로 호출해야 해서, **hr-portal이 처음으로 백엔드를 갖게 됐다**.

구조:
```
브라우저 → hr.abyz-lab.work/api/*  (nginx가 리버스 프록시, 새 Cloudflare 규칙 필요 없음)
              → api 컨테이너(Flask, backend/)  → Naver/Opinet API (키는 서버 .env에만 존재)
브라우저 → hr.abyz-lab.work/*      (nginx가 public/ 정적 서빙, 기존과 동일)
```

- `backend/`: Flask 앱. `/api/calculate`(핵심 계산), `/api/search-places`, `/api/export/{excel,pdf,application-form}`.
- `nginx.conf`: `/api/`만 `api` 컨테이너로 proxy_pass, 나머지는 기존처럼 정적 서빙.
- `docker-compose.yml`: `api` 서비스는 `expose`만 하고 host 포트를 열지 않음 (nginx만 접근 가능, 새 Cloudflare Tunnel 규칙 불필요).
- API 키 5개(NAVER_CLIENT_ID/SECRET, NAVER_LOCAL_CLIENT_ID/SECRET, OPINET_API_KEY)는 저장소 루트 `.env`에서 `${VAR:-}` 치환으로 주입 — CI에는 `.env`가 없어도 빈 값으로 통과한다(계산 API를 실제로 호출하는 스모크 테스트는 없음).

**서버 반영 완료 (2026-08-07)** — SSH 키를 등록해서 이제 나도 서버(`hr-portal-server` — `~/.ssh/config`에 별칭 등록됨, `10.20.6.50`, 사용자 `abyz-lab0`)에 직접 접속해 작업할 수 있다. 해둔 것:
1. `.env` 생성 완료 (`secom-allowance-calculator/project/.env`에서 그대로 복사, 나는 내용을 읽지 않고 scp로만 전달했음 — 앞으로도 시크릿은 이 방식으로, 절대 `cat`/`Read`로 값 자체를 내 컨텍스트에 띄우지 않는다).
2. `docker compose up -d --build` 완료, `api`/`web` 컨테이너 정상 기동 확인.
3. `/usr/local/bin/hr-portal-deploy.sh`에 `docker compose up -d --build` 반영 완료 (기존 스크립트는 날짜 붙여 백업).

앞으로 `backend/`가 바뀌는 PR이 merge되면 2분 뒤 서버가 알아서 재빌드까지 한다 — 더 이상 수동 개입 불필요.

## 규정 검색 (서버 전용 파일 패턴)

규정 원문(PDF/DOCX, 인사규정·급여규정·정보보안규정 등)은 **저장소에 절대 올리지 않는다** — hr-portal이 Public 저장소라 사내 규정을 영구 공개하게 된다. `.env`와 같은 패턴: 서버의 `regulations/` 폴더(gitignore됨, `regulations/README.md`만 예외)에 관리자가 SSH로 직접 넣어야 한다. 파일명은 `backend/rules_config.py`의 `RULES` 목록과 정확히 일치해야 한다.

- `backend/services/rules_search.py`: PDF는 `pypdf`, DOCX는 `python-docx`로 텍스트를 뽑아 제목+본문에서 검색어를 찾는다. 파일이 없으면 제목만으로 매칭하고 `available:false`로 표시 — 서버가 죽지 않는다.
- `GET /api/rules/search?q=`, `GET /api/rules/<id>/file` (PDF는 `inline`으로 새 탭에 열리고, DOCX는 브라우저가 못 미리보기 하니 `attachment`로 받는다).
- **서버에 원문 11개 파일 업로드 완료 (2026-08-07)** — SSH 키 등록 후 로컬 파일을 scp로 직접 전달함. 절차는 `regulations/README.md`에 그대로 있음 (새 규정 추가/교체할 때 재사용).

**교훈**: `.topbar-search`는 이름 그대로 상단바 전용이다. 모바일 CSS에서 `.topbar-search{display:none}`으로 숨기는 규칙이 있어서, 이 클래스를 콘텐츠 영역(규정검색/구성원검색 등) 검색창에 재사용하면 좁은 화면에서 통째로 사라진다. 콘텐츠 안의 검색창은 `.card` + `.field` + `.btn`으로 만든다.

## 현재 사이트 상태 (2026-08-07 기준)

- 사이드바 메뉴 순서: 홈 - 급여·계약 - 평가·성과 - 규정 검색 - 출장·경비 - 조직도 - 공지사항 - 마이페이지
- **근태·휴가 메뉴는 삭제됨** (요구사항 미확정 상태로 방치되어 있었음). 나중에 다시 만들려면 NAV 배열에 새로 추가해야 한다 — 예전 구현은 git 히스토리에만 남아있다.
- 라이트 테마 + H&abyz 로고(4색 핀휠 마크) + 홈 히어로 배너로 리디자인 완료. 모바일 반응형(햄버거 사이드바) 적용 완료.
- **급여·계약 / 평가·성과 / 마이페이지 / 공지사항**: 서브탭 구조만 유지되고 내용은 전부 "열심히 개발 중입니다" placeholder(`comingSoon()` 함수)로 비워둔 상태. 실제 요구사항이 확정되면 그때 채운다. 홈 화면 공지사항 카드도 목록 없이 제목만 있음.
- **출장·경비 > 출장 신청 탭 삭제됨** (경비 산출기 / 정산 내역만 남음).
- **규정 검색**: 실제 백엔드 연동 + 서버 원문 업로드까지 완료(위 섹션). 전문 검색·전문 보기 다 실제로 동작함.
- 홈 "빠른 서비스" 5개 버튼은 실제 메뉴로 연결되어 있다 (`goTo(navId, tabId)`).

## 모듈 구현 순서

README 로드맵과 beginner-guide 8-2절 기준. 쉬운 것부터, 순서를 건너뛰지 않는다.

1. 공지사항 (CRUD 연습, 개인정보 없어 안전) — 아직 착수 전
2. 로그인 / 직원 정보 / 역할 구분
3. 전자결재 (요청/승인/반려)
4. 출장·경비 — 경비 산출기는 백엔드 완료(위 섹션 참고, 서버 반영 작업 남음), 출장 신청/정산 내역은 아직 목업
5. 급여·계약 / 평가·성과 / 마이페이지 (현재 placeholder — 요구사항 확정 후 채움, 가장 민감하니 마지막)

각 모듈 시작 전 반드시 사용자에게 **정의(한 문장) + 화면 구성 + 저장할 정보 + 지켜야 할 규칙**을 확정받는다. 규칙이 빠지면 만들지 않는다.

## 알려진 결함 / 갭

README §6(알려진 결함), §5(로드맵 Phase 1~7)에 상세 목록 있음. 새 기능 구현 시 관련 결함이 있으면 같이 언급한다.
