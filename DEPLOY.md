# 배포 안내

hr-portal이 어떻게 서버에 반영되는지, 문제가 생기면 어떻게 되돌리는지 정리합니다.

작업 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를 먼저 보세요.

---

## 1. 평소에는 아무것도 안 해도 됩니다

PR이 merge되면 나머지는 자동입니다.

```
main merge
  → CI 통과      → release 브랜치 자동 갱신
  → 포털 서버의 타이머(2분 주기)가 release 를 가져감
  → 바뀐 게 있으면 컨테이너 재빌드
  → https://hr.abyz-lab.work 반영
```

**보통 merge 후 2분 안에 반영됩니다.** 수동 개입이 필요한 경우는 아래 3장뿐입니다.

| 무엇이 바뀌었나 | 반영 방식 |
|---|---|
| `public/` (화면) | 바인드 마운트라 **재빌드 없이** 즉시 |
| `backend/` (계산·검색) | 이미지 **재빌드 후** 반영 (자동) |
| `docker-compose.yml`·`nginx.conf` | 재빌드 후 반영 (자동) |
| **서버 `.env`** | **자동 아님** — 사람이 직접 (3장) |
| **`regulations/` 원문** | **자동 아님** — 사람이 직접 (4장) |
| **직원 명단 가져오기** (하이웍스 → `data/`) | **자동 아님** — 서버 타이머로 하루 1회 (9장) |
| **휴대폰 알림** 열쇠(`.env`) · 평일 9시 타이머 | **자동 아님** — 서버에서 사람이 한 번 (10장) |
| **`data/` 직원·로그인 DB** | 계산 컨테이너가 처음 뜰 때 `data/hr-portal.db`를 만들고, 새 표가 생기면 시작할 때 더한다(자동). 재빌드해도 남는다. **이 폴더를 지우면 직원·로그인 정보가 모두 사라진다** — 백업 절차는 로그인 설계(PR #51) 9장 7번 |

---

## 2. 지금 상태 확인하기

```bash
curl -s https://hr.abyz-lab.work/api/health
```

`{"status":"ok"}` 가 나오면 계산·검색 백엔드가 정상입니다.

포털 서버에서 더 자세히 보려면:

```bash
cd ~/workspace/github-hnabyz-bot/hr-portal
docker compose ps                    # web / api 둘 다 Up 이어야 정상
docker compose logs api --tail 50    # 오류 확인
git log -1 --format='%h %s'          # 지금 서버에 올라간 버전
```

---

## 3. 서버 `.env` — 처음 한 번, 또는 키가 바뀔 때

계산 백엔드는 외부 서비스(네이버 지도·지역검색, 오피넷)를 호출하므로 **API 키 5개**가 필요합니다. 하이웍스 연동용 변수 2개(`HIWORKS_OFFICE_TOKEN`, `HIWORKS_DOMAIN`)도 같은 파일에 둡니다 ([하이웍스 가이드](docs/hiworks-api-setup.md)). 키는 **저장소에 없고 서버 파일에만 존재합니다.**

`.env.example`을 복사해 값을 채웁니다.

```bash
cd ~/workspace/github-hnabyz-bot/hr-portal
cp .env.example .env
nano .env
```

필요한 변수 이름은 `.env.example`에 있습니다. **값 자체는 어디에도 기록하지 마세요.**

### 제대로 들어갔는지 (값을 화면에 띄우지 않고) 확인

```bash
awk -F= '/^[A-Z]/{print $1": "length($2)"자"}' .env
```

7줄(네이버·오피넷 5 + 하이웍스 2) 모두 `0자`가 아니면 성공입니다. **하나라도 `0자`면 다시 넣어야 합니다.** (`#`로 시작하는 안내 줄은 출력되지 않습니다)

### 반영

```bash
docker compose up -d --build
curl -s https://hr.abyz-lab.work/api/health
```

### ⚠️ 흔한 실수

```
NAVER_CLIENT_ID=abc123        ← 맞음
NAVER_CLIENT_ID = abc123      ← = 앞뒤 띄어쓰기
NAVER_CLIENT_ID="abc123"      ← 따옴표
NAVER_CLIENT_ID=abc123        ← 끝에 공백 (눈에 안 보임)
```

---

## 4. 규정 원문 — 새 규정을 넣거나 교체할 때

규정 원문(PDF/DOCX)은 **저장소에 절대 올리지 않습니다.** 서버의 `regulations/` 폴더에 직접 넣습니다.

```bash
scp "인사규정(2026.05).pdf" <포털서버>:~/workspace/github-hnabyz-bot/hr-portal/regulations/
```

**파일명이 글자 하나까지 정확히 일치해야 합니다.** 목록은 `backend/rules_config.py`의 `RULES`에 있습니다. 숫자 뒤 마침표, 띄어쓰기, 괄호, 밑줄까지 그대로여야 합니다.

파일이 없으면 서비스가 죽지는 않고, **제목으로만 검색되며 "전문 보기" 버튼이 안 뜹니다.**

자세한 절차: `regulations/README.md`

---

## 5. 배포가 안 될 때

| 증상 | 원인 | 확인 |
|---|---|---|
| merge 후 2분 넘게 그대로 | CI 실패로 release 미갱신 | GitHub Actions 탭에서 빨간불 확인 |
| `/api/health` 가 **502** | 계산 컨테이너가 떴다가 죽음 | 대부분 `.env` 오타. `docker compose logs api --tail 50` |
| `/api/health` 가 **404** | 계산 컨테이너 미기동 | `docker compose ps` 로 `api` 확인 |
| 계산 시 "주소를 찾을 수 없습니다" | 네이버 키 문제 또는 주소 형식 | 도로명 주소로 시도. 장소명은 검색 버튼을 거쳐야 함 |
| 계산 시 "유가 정보를 가져올 수 없습니다" | 오피넷 키 문제 | `OPINET_API_KEY` 재확인 |
| 규정 "전문 보기" 버튼 없음 | 원문 파일 없거나 파일명 불일치 | 4장 참고 |

서버 타이머 상태:

```bash
systemctl status hr-portal-deploy.timer
journalctl -u hr-portal-deploy.service -n 20
```

---

## 6. 되돌리기

### 방법 1 — revert (권장)

잘못된 변경을 되돌리는 **새 커밋**을 만들어 PR로 올립니다. 히스토리가 남아 나중에 무슨 일이 있었는지 알 수 있습니다.

```bash
git revert <되돌릴-커밋-해시>
```

merge 후 2분이면 서버까지 자동으로 되돌아갑니다.

### 방법 2 — 화면만 임시로 살리기 (긴급)

계산 백엔드가 계속 죽어서 사이트 전체가 영향받을 때, 화면만 띄웁니다.

```bash
cd ~/workspace/github-hnabyz-bot/hr-portal
docker compose down
docker compose up -d web
```

경비 계산·규정 검색은 멈추지만 나머지 화면은 정상입니다. **원인을 고친 뒤 `docker compose up -d --build`로 복구합니다.**

---

## 7. 작업할 때 주의 — 저장소 폴더는 배포 대상입니다

포털 서버의 `~/workspace/github-hnabyz-bot/hr-portal` 은 **실제 서비스가 도는 폴더**이고, 타이머가 **2분마다 `git reset --hard origin/release`** 를 실행합니다.

**이 폴더에서 브랜치를 만들어 작업하면 커밋하지 않은 변경이 최대 2분 안에 사라집니다.**

코드 작업은 별도 폴더(워크트리)에서 합니다.

```bash
git -C ~/workspace/github-hnabyz-bot/hr-portal worktree add --detach ~/workspace/hr-portal-dev origin/main
```

저장소 폴더는 **서비스 전용**으로 두고, `.env` 수정과 `docker compose` 실행에만 씁니다.

같은 서버에 **무관한 다른 서비스가 함께 떠 있습니다.** `docker` 명령을 쓸 때는 반드시 `docker compose`(이 폴더 기준)로 실행하고, 다른 컨테이너는 건드리지 마세요.

---

## 8. 로그인 (하이웍스 SSO) — 4단계부터

- **모든 `/api/*`는 로그인한 사람만 쓴다.** 세션 없이 열린 곳은 `/api/health`와 로그인 받는 곳(`/api/auth/hiworks`, `/api/auth/message`)뿐이다. 그래서 하이웍스 메뉴를 켜기(6단계) 전에는 규정 검색·경비 산출기도 쓸 수 없다.
- `.env` 새 값: `HIWORKS_FIRST_HR_USER_NO`(첫 HR의 하이웍스 사용자 번호 — 가이드 8장 "첫 HR 번호 넣기"), 필요할 때만 `HIWORKS_SSO_LINK_BY_EMPLOYEE_NO=1`.
- **`HIWORKS_LINK_API_BASE`를 서버 `.env`에 넣지 않는다** — 운영 모드(`docker-compose.yml`의 `HR_ENV=production`)에서 이 값이 있으면 api가 뜨지 않는다(로그에 이유가 나온다).
- 로그인이 안 될 때: `docker compose logs api --tail 50`. 안내 화면 문구는 `backend/auth.py`의 `MESSAGES`.

---

## 9. 직원 명단 가져오기 — 하이웍스에서 하루 1회

하이웍스의 직원 명단·조직도를 포털 DB(`data/`)에 맞춘다. 새 사람 추가(포털 사용은 꺼진 채) · 소속·연락처 고치기 · 사라진 사람 비활성. 규칙은 로그인 설계(PR #51) 11장. **`.env`에 `HIWORKS_OFFICE_TOKEN`·`HIWORKS_DOMAIN`이 있어야 한다**(3장).

```bash
cd ~/workspace/github-hnabyz-bot/hr-portal
docker compose exec -T api python -m hr_eval.roster_sync           # 미리 보기 — 무엇이 바뀔지 숫자만, DB는 그대로
docker compose exec -T api python -m hr_eval.roster_sync --apply   # 반영
```

| 나오는 것 | 뜻 |
|---|---|
| `[미리 보기 …]` / `[반영 완료]` + 숫자 | 부서 추가·변경·닫기, 직원 추가·정보 변경·비활성, 올리지 않은 사람 수. **이름·번호·연락처는 나오지 않는다** |
| `[멈춤] …` (끝 코드 1) | 하이웍스 응답을 믿을 수 없어 **아무것도 바꾸지 않았다**(실패 코드·빈 목록·3단계 조직 등). `--apply`였으면 감사로그 `ROSTER_SYNC_FAILED` |
| `[멈춤] 한 번에 N명이 사라졌습니다` | 하루 10명 넘게 사라지면 멈춘다. 실제 퇴사라면 `--apply --allow-mass`로 한 번 |

- **처음에는 미리 보기부터** 돌려 숫자가 맞는지 본다(직원 약 100명, 부서 20개). "회사 바로 아래(올리지 않음)" 숫자가 있으면 그 사람들은 부서가 없어 올라가지 않는다 — 인사담당자에게 알린다.
- 하루 1회는 서버 타이머로 건다(예: 매일 06:00에 `--apply`). 배포 타이머와 같은 방식으로 두면 된다. 하이웍스 호출 한도는 하루 1,000건이고 한 번에 2건을 쓴다.
- 퇴사자는 다음 날까지 기다리지 않고 HR이 포털에서 당일에 비활성화한다(로그인 설계 §4).

---

## 10. 휴대폰 알림 — 열쇠 한 번 · 평일 아침 9시 타이머

휴대폰에 설치한 인사포털로 처리할 일 알림을 보낸다(설계: `docs/superpowers/specs/2026-10-22-push-notifications-design.md`). **서버에서 사람이 한 번만 하면 된다 — 자동이 아니다.** 열쇠를 넣지 않으면 알림 보내기만 조용히 건너뛰고(휴대폰 홈에 "알림 켜기" 칸도 나오지 않는다) 나머지 기능은 그대로 된다. 타이머를 만들지 않으면 아침 9시 묶음 알림만 오지 않는다(일이 생길 때 바로 가는 알림은 온다).

### ① 열쇠 만들기 — 한 번

```bash
cd ~/workspace/github-hnabyz-bot/hr-portal
docker compose exec -T api python -m push keygen
```

`HR_PUSH_PUBLIC=…` · `HR_PUSH_PRIVATE=…` 두 줄이 나온다. **`keygen`은 개인키를 터미널 화면에 한 번 그대로 찍는다** — 옆에서 보는 사람이 없고 화면을 공유하지 않을 때 돌리고, 붙여 넣은 뒤 바로 `clear` 한다. 이 두 줄을 서버 `.env`의 같은 이름 줄(`.env.example`에 비어 있는 채로 있다)에 붙인다. 서버 `.env`에 그런 줄이 없으면 새 줄로 더한다. 그리고 다시 띄운다.

```bash
nano .env                                  # 두 줄 붙여 넣기 (= 앞뒤 띄어쓰기 · 따옴표 없이 — 3장 "흔한 실수")
clear                                      # 화면에 남은 열쇠 지우기
awk -F= '/^HR_PUSH/{print $1": "length($2)"자"}' .env    # 값은 보이지 않고 글자 수만 — 둘 다 0자가 아니면 성공
docker compose up -d
```

- **`HR_PUSH_PRIVATE`(개인키)는 서버 `.env`에만 있어야 한다.** 채팅 · 이슈 · 메일 · PR · 화면 캡처 · 터미널 공유 어디에도 보이거나 붙이지 않는다(값을 확인할 때는 위 `awk`처럼 글자 수만 본다). 공개키(`HR_PUSH_PUBLIC`)는 휴대폰에 알려 주는 값이라 숨길 필요는 없지만 같은 줄에 붙어 나오므로 함께 조심한다.
- **열쇠를 바꾸면 휴대폰마다 등록을 새로 해야 한다.** 옛 열쇠로 한 휴대폰 등록은 새 열쇠로 보낸 알림을 받지 못한다. 직원이 누를 단추는 없다 — **휴대폰이 인사포털 앱을 다음에 열 때 저절로** 옛 등록을 풀고 새 열쇠로 다시 등록한다(알림을 이미 허용한 휴대폰은 묻는 창도 뜨지 않는다). 그 전까지는 그 휴대폰으로 알림이 가지 않는다 — 앱을 열지 않은 사람은 열 때까지 못 받는다. 그래서 열쇠는 한 번 만들면 바꾸지 않는다(잃어도 다시 만들 수는 있다).

### ② 평일 아침 9시 타이머 — 한 번

매일 아침 "처리할 일이 N건 있어요" 묶음 알림을 보내는 명령(`python -m push morning`)을 서버 타이머로 건다. 앱 안에는 예약 작업을 두지 않는다(계산 컨테이너의 작업자가 둘이라 두 번 돈다). 이 명령은 **평일 9~18시(한국 시간)에만 보내고, 같은 날 두 번 돌지 않으며**, 처리할 일이 1건 이상인 사람에게만 보낸다. 출력은 보낸 사람 수 하나뿐이다. **오늘을 보내기 전에 먼저 기록한다**(`push_morning` 표) — 두 실행이 겹쳐도 같은 사람에게 두 번 가지 않는다. 대신 보내는 도중에 멈추면(서버 재시작 등) 그날 묶음은 일부만 가고 다시 돌지 않는다(두 번 울리는 것보다 낫다고 본 선택 — 그날 기록 줄을 지우면 다시 보낼 수 있지만 이미 받은 사람은 한 통 더 받는다).

배포 타이머(`hr-portal-deploy`)와 같은 곳(`/etc/systemd/system/`)에 파일 둘을 만든다. **`User=` 줄과 저장소 경로는 배포 서비스 파일을 그대로 따른다** — 먼저 열어서 본다.

```bash
systemctl cat hr-portal-deploy.service     # User= 줄과 저장소 경로를 확인
```

`/etc/systemd/system/hr-portal-push-morning.service`

```ini
[Unit]
Description=인사포털 아침 9시 처리할 일 알림 (휴대폰)

[Service]
Type=oneshot
User=<배포 서비스와 같은 사용자>
ExecStart=/usr/bin/docker compose -f <저장소 경로>/docker-compose.yml exec -T api python -m push morning
```

`/etc/systemd/system/hr-portal-push-morning.timer`

```ini
[Unit]
Description=평일 아침 9시 처리할 일 알림 (휴대폰)

[Timer]
OnCalendar=Mon..Fri 09:00 Asia/Seoul
Persistent=false

[Install]
WantedBy=timers.target
```

- `<저장소 경로>`는 2장의 폴더(`~/workspace/github-hnabyz-bot/hr-portal`)의 **절대 경로**다 — systemd는 `~`를 풀지 않는다.
- **`Persistent=false`는 일부러 고른 것이다.** 서버가 9시에 꺼져 있으면 **그날은 아침 묶음 알림이 없다**(다음 평일 묶음에 밀린 일이 모두 들어간다). 켜진 뒤 뒤늦게 돌려 주는 방식(`Persistent=true`)은 쓰지 않는다 — 근무시간(평일 9~18시) 밖이면 명령이 어차피 아무것도 안 보내고, 근무시간 안이어도 갑자기 늦게 울리는 것보다 그날은 안 오는 편이 낫다고 보기 때문이다. 밤에 휴대폰이 울리는 일은 절대 없어야 한다.
- 시각은 서버 시계(UTC일 수 있다)와 상관없이 한국 시간 9시다(`Asia/Seoul`). 공휴일은 따로 빼지 않는다(평일이면 돈다).

켜기:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hr-portal-push-morning.timer
```

### ③ 확인

```bash
systemd-analyze calendar "Mon..Fri 09:00 Asia/Seoul"       # 타이머 줄 문법 검사 — 파일을 만들기 전에 (Normalized form · Next elapse 가 나오면 맞는 문법)
systemctl list-timers | grep push          # 다음 실행이 다음 평일로 잡혀 있으면 성공
sudo journalctl -u hr-portal-push-morning.service -n 20    # 지난 실행 이력 (보낸 사람 수)
```

- **`list-timers`는 서버 시계의 시각으로 보여 준다.** 서버가 UTC이면 한국 시간 평일 09:00이 `00:00 UTC`(같은 평일 0시)로 보인다 — 틀린 게 아니다. 시간대 이름을 붙인 줄(`Asia/Seoul`)이라 서버 시계와 상관없이 한국 9시에 돈다.

- 지금 바로 한 번 돌려 보려면 `sudo systemctl start hr-portal-push-morning.service`. **시험용이 아니다 — 평일 9~18시이고 오늘 아직 안 돌았으면 진짜로 보내고, 그날은 9시 타이머가 다시 보내지 않는다.** 평일 9~18시가 아니거나 오늘 이미 돌았으면 `0`만 나오고 아무것도 보내지 않는다(정상 — 밤·주말에 울리지 않고 같은 날 두 번 울리지 않게 하는 규칙).
- 처음에는 직원이 휴대폰에서 "휴대폰 알림 켜기"를 눌러 둬야 받는 사람이 생긴다. 아무도 안 켰으면 `0`이 나오는 것도 정상이다.
- 타이머를 잠시 끄려면 `sudo systemctl stop hr-portal-push-morning.timer`.

---

## 11. 급여 비밀번호 — HR이 한 명뿐일 때

급여명세서 · 원천징수영수증 · 전자연봉계약서 메뉴는 직원마다 6자리 숫자 비밀번호로 잠긴다(설계: `docs/superpowers/specs/2026-10-22-pay-pin-lock-design.md`). **배포할 때 서버에서 할 일은 없다 — ssh 없이 merge만으로 반영된다.** 새 열쇠를 만들지 않고(번호 전용 열쇠는 서버 `.env`의 `HR_PAY_KEY`에서 서버가 뽑는다 — 이름은 `.env.example`에 있다) 새 타이머도 없으며, 새 표(DB 판 18)는 서버가 뜰 때 저절로 만든다. `HR_PAY_KEY`가 아직 없으면 잠금도 없다(3장).

- **`HR_PAY_KEY`를 바꾸면 모든 직원의 비밀번호가 맞지 않게 된다**(번호 열쇠가 거기서 나온다) — 모두 HR 초기화가 필요하고, HR이 한 명뿐이면 그 HR은 아래 명령으로 초기화한다.
- 비밀번호를 잊었거나 5번 틀려 잠긴 직원은 **HR이 포털의 구성원 설정에서 초기화**한다(이름 창의 "급여 비밀번호" 줄). 초기화하면 그 사람은 다음에 들어갈 때 본인이 새로 정한다 — HR도 번호는 볼 수 없다.
- **HR 본인**의 비밀번호는 다른 HR이 같은 곳에서 초기화한다. **HR이 한 명뿐이면** 포털에서 해 줄 사람이 없으니 서버에서 한 번 명령을 돌린다.

```bash
cd ~/workspace/github-hnabyz-bot/hr-portal
docker compose exec -T api python -m paypin reset <그 HR의 회사 메일 주소>
```

| 나오는 것 | 뜻 |
|---|---|
| `초기화했습니다 (직원 번호 N)` | 그 사람의 비밀번호를 지웠다. 풀려 있던 그 사람의 창도 모두 다시 잠긴다 — 다음에 급여·계약 메뉴를 열 때 새로 정한다. 감사로그에 `PAY_PIN_RESET`(부른 사람 없음)이 남는다 |
| `그 메일 주소의 직원이 없습니다` (끝 코드 1) | **아무것도 바꾸지 않았다.** 메일 주소가 포털 명단과 같은지 본다(대문자·소문자는 가리지 않는다). 대소문자만 다른 주소가 둘이면 누구인지 알 수 없어 역시 아무도 건드리지 않는다 |

- 메일 주소는 **직원 명단(구성원 설정)에 있는 회사 메일 주소**다. 이 명령은 비밀번호를 지울 뿐 새 번호를 정하지 않는다.
- 꼭 `docker compose exec`로 돌린다. 컨테이너 밖에서 돌리면 `HR_DB_PATH 가 없습니다` 또는 `DB 파일이 없습니다`라고 알리고 끝 코드 1로 멈춘다(빈 DB를 만들지 않는다).
