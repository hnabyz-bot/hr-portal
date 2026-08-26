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

계산 백엔드는 외부 서비스(네이버 지도·지역검색, 오피넷)를 호출하므로 **API 키 5개**가 필요합니다. 키는 **저장소에 없고 서버 파일에만 존재합니다.**

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

5줄 모두 `0자`가 아니면 성공입니다. **하나라도 `0자`면 다시 넣어야 합니다.**

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
