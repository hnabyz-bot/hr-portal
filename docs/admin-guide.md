# 관리자 가이드 — 서버 운영

서버에 직접 접근해야 하는 담당자를 위한 문서다.
일반 작업자는 [초심자 가이드](beginner-guide.md)만으로 충분하며, 이 문서를 볼 필요가 없다.

**전제 조건**
- Tailscale 네트워크에 포함되어 있을 것
- 서버 계정(`abyz-lab0`) 접근 권한이 있을 것

---

## 1. 서버 접속

### 접속

Windows 터미널(시작 → `터미널`)에서 실행한다.

```
ssh abyz-lab0@100.73.145.44
```

비밀번호 입력 시 화면에 아무것도 표시되지 않는 것이 정상이다.

접속 후 프롬프트가 `abyz-lab0@abyz-lab0-OptiPlex-3060:~$` 로 바뀐다.
**이후 입력하는 명령은 내 PC가 아니라 서버에서 실행된다.**

### 프로젝트 폴더

```
cd ~/workspace/github-hnabyz-bot/hr-portal
```

### 접속 종료

```
exit
```

### 접속 실패 시

| 증상 | 원인 |
|---|---|
| `Connection timed out` | Tailscale 미연결 |
| `Permission denied` | 비밀번호 오류 또는 키 미등록 |
| `Could not resolve hostname` | 주소 오타 |

---

## 2. 서버 환경

| 항목 | 값 |
|---|---|
| 호스트명 | `abyz-lab0-OptiPlex-3060` |
| OS | Ubuntu 24.04.4 LTS |
| Tailscale 주소 | `100.73.145.44` |
| 프로젝트 경로 | `~/workspace/github-hnabyz-bot/hr-portal` |
| 서비스 주소 | https://hr.abyz-lab.work |
| 로컬 바인딩 | `127.0.0.1:8080` |

### 같은 서버의 다른 서비스

이 서버에는 `voc-mr-ecr` 스택(컨테이너 4개)과 호스트 nginx가 함께 돌고 있다.
**우리 서비스와 완전히 분리되어 있으므로 건드리지 않는다.**

| 서비스 | 포트 | 비고 |
|---|---|---|
| `voc-mr-ecr-web` | 127.0.0.1:5173 | 별도 서비스 |
| `voc-mr-ecr-api` | 127.0.0.1:3000 | 별도 서비스 |
| 호스트 nginx | 0.0.0.0:80 | `voc-mr-ecr` 전용 |
| **hr-portal-web** | **127.0.0.1:8080** | **우리 것** |

---

## 3. 구성 이해

```
인터넷
  ↓
Cloudflare Edge (icn01 / icn05 / icn06)
  ↓
Cloudflare Tunnel  "hr-portal-dev"
  tunnel id: 1570ba07-e6dd-4535-9929-9ab7b7daa82d
  설정: /etc/cloudflared/config.yml
  ↓
127.0.0.1:8080
  ↓
Docker 컨테이너 "hr-portal-web" (nginx:alpine)
  ↓
~/workspace/github-hnabyz-bot/hr-portal/public/
```

### 설계 원칙

- **호스트 nginx를 경유하지 않는다.** 터널의 ingress 규칙이 호스트명 라우팅을, Cloudflare가 TLS 종단을 담당한다
- **`public/` 만 컨테이너에 마운트한다.** 저장소 루트를 마운트하면 `.git` 이 웹으로 노출된다
- **`127.0.0.1` 로만 바인딩한다.** cloudflared만 접근하면 되므로 사내망에도 열지 않는다
- **서비스 1개 = compose 스택 1개 + 로컬 포트 1개 + ingress 규칙 1줄**

---

## 4. 일상 운영

모든 명령은 프로젝트 폴더에서 실행한다.

```
cd ~/workspace/github-hnabyz-bot/hr-portal
```

### 컨테이너

| 명령 | 하는 일 |
|---|---|
| `docker compose ps` | 상태 확인 |
| `docker compose up -d` | 시작 |
| `docker compose down` | 중지 |
| `docker compose restart` | 재시작 |
| `docker compose logs --tail 50` | 최근 로그 |

### 터널

| 명령 | 하는 일 |
|---|---|
| `sudo systemctl status cloudflared` | 상태 확인 |
| `sudo systemctl restart cloudflared` | 재시작 |
| `sudo journalctl -u cloudflared -n 50` | 최근 로그 |

### 배포 (자동)

배포는 자동이다. 수동으로 `git pull` 하지 않는다.

```
main 병합 → CI 검사 통과 → release 갱신 → 서버 타이머(2분) → 반영
```

서버 저장소는 **`release` 브랜치를 체크아웃한 상태**다.
`release`는 CI가 검사를 통과했을 때만 갱신되므로, 검사에 실패한 코드가 서버에 도달할 경로는 없다.

| 명령 | 하는 일 |
|---|---|
| `systemctl list-timers hr-portal-deploy` | 다음 실행 시각 확인 |
| `sudo journalctl -u hr-portal-deploy -n 30` | 배포 이력 |
| `sudo systemctl start hr-portal-deploy.service` | 즉시 배포 (2분 대기 생략) |
| `sudo systemctl stop hr-portal-deploy.timer` | 자동 배포 일시 중지 |

**구성 파일**

| 경로 | 내용 |
|---|---|
| `/usr/local/bin/hr-portal-deploy.sh` | 배포 스크립트 |
| `/etc/systemd/system/hr-portal-deploy.service` | 실행 단위 |
| `/etc/systemd/system/hr-portal-deploy.timer` | 2분 주기 타이머 |

> 배포 스크립트는 `git reset --hard origin/release` 를 수행한다.
> **서버 저장소에서 직접 파일을 고치면 다음 배포 때 사라진다.** 수정은 반드시 GitHub을 통해서 한다.

---

## 5. 점검

### 계층별 확인

문제가 생기면 **안쪽부터 바깥쪽으로** 확인한다.

**① 컨테이너**
```
docker compose ps
```

**② 컨테이너 응답**
```
curl -I http://127.0.0.1:8080/
```
`200 OK` 면 컨테이너는 정상이다.

**③ 터널**
```
sudo systemctl status cloudflared
```
`active (running)` 확인.

**④ 외부 경로 전체**
```
curl -I https://hr.abyz-lab.work
```

### 결과 해석

| ② | ④ | 원인 |
|---|---|---|
| 200 | 200 | 정상 |
| 200 | 실패 | 터널 문제 |
| 실패 | 실패 | 컨테이너 문제 |
| 실패 | 200 | 캐시. `Ctrl+F5` 로 재확인 |

---

## 6. 리눅스 기본 명령

| 명령 | 하는 일 |
|---|---|
| `pwd` | 현재 위치 |
| `ls -la` | 파일 목록 (자세히) |
| `cd 폴더명` | 이동 (`cd ..` 상위, `cd ~` 홈) |
| `cat 파일명` | 내용 출력 |
| `less 파일명` | 스크롤해서 보기 (`q` 로 종료) |
| `nano 파일명` | 편집 (`Ctrl+O` 저장, `Ctrl+X` 종료) |
| `cp` / `mv` / `rm` | 복사 / 이동 / 삭제 |

**요령**
- `Tab` — 파일명 자동 완성
- `↑` — 이전 명령 불러오기
- `Ctrl + C` — 실행 중단

> `rm -rf` 는 되돌릴 수 없다. 확신이 없으면 쓰지 않는다.

---

## 7. 작업자 합류시키기

서버 접근 권한(Tailscale) 없이 GitHub만으로 작업하게 하는 절차다.

### 7-1. 사전 준비

작업자를 초대하기 전에 **Phase 1.5(협업 환경 구축)를 먼저 완료한다.**
자동 검사·미리보기·자동 배포가 없으면 초심자 가이드의 4~6장이 동작하지 않는다.

### 7-2. 작업자에게 요청할 것

작업자 본인이 GitHub 계정을 만들어야 한다. 절차는 [초심자 가이드 2장](beginner-guide.md)에 있다.

```
초심자 가이드 2장을 보고 GitHub 계정을 만든 뒤,
2단계 인증까지 설정하고 사용자 이름을 알려주세요.
```

> **2단계 인증은 선택이 아니다.** GitHub이 코드 기여자에게 의무화하고 있어,
> 설정하지 않으면 일정 기간 후 작업이 제한된다.

### 7-3. 저장소에 초대

**Settings → Collaborators → Add people** 에서 사용자 이름으로 검색해 추가한다.

권한 등급은 다음과 같다.

| 등급 | 할 수 있는 것 | 비고 |
|---|---|---|
| Read | 보기만 | 참관자 |
| Triage | 이슈 관리 | 잘 쓰지 않음 |
| **Write** | 브랜치 생성, PR 생성, 병합 | **작업자에게 이 등급을 준다** |
| Maintain | 저장소 설정 일부 | 팀 리더 |
| Admin | 전체 | 관리자만 |

**Write 를 부여한다.** `main` 직접 push는 브랜치 보호 규칙으로 따로 막는다.
보호 규칙이 없으면 Write 권한자가 `main`에 바로 push할 수 있으므로, **보호 규칙 설정이 선행되어야 한다.**

초대 후 작업자가 이메일에서 `Accept invitation` 을 눌러야 접근이 시작된다.

### 7-4. 권한 회수

**Settings → Collaborators** 에서 해당 사용자의 `Remove` 를 누른다.
즉시 접근이 차단되며, 기존 커밋 기록은 그대로 남는다.

### 7-5. 조직(Organization) 전환 검토

현재 저장소는 개인 계정(`hnabyz-bot`) 소유다. 인원이 늘어나면 조직 계정으로 옮기는 것을 검토한다.

| 항목 | 개인 계정 | 조직 |
|---|---|---|
| 소유권 | 계정 하나에 종속 | 회사 자산으로 관리 |
| 팀 단위 권한 | 불가 | 가능 |
| 담당자 변경 | 번거로움 | 용이 |

> 브랜치 보호 규칙은 플랜에 따라 지원 여부가 다르다. 전환 전에 Settings에서 실제 사용 가능한지 확인한다.

---

## 8. 접근 권한 관리 (서버)

### SSH 키 회수

작업자에게 부여한 접근을 회수하려면 해당 줄을 삭제한다.

```
nano ~/.ssh/authorized_keys
```

주석(줄 끝의 이름)으로 구분해 해당 줄만 지운다.

### 현재 등록된 키 확인

```
cat ~/.ssh/authorized_keys
```

---

## 9. 미완료 항목

아래는 아직 구성되지 않았다. 진행 상황은 [README](../README.md)의 로드맵을 참고한다.

| 항목 | 현재 상태 | 영향 |
|---|---|---|
| **경비 산출기 백엔드 서버 반영** | 미완료 | `docker-compose.yml`에 `api` 서비스(Flask)가 추가됐지만, 서버에 `.env`(API 키) 생성 + `docker compose up -d --build` 최초 실행 + 배포 스크립트에 빌드 단계 추가가 아직 안 됐다. 상세 절차는 [CLAUDE.md](../CLAUDE.md)의 "백엔드가 있는 기능" 참고 |
| **브랜치 보호 규칙** | 미설정 | Write 권한자가 `main`에 바로 push할 수 있다. **작업자 초대 전 필수** |
| **미리보기 환경** | 미구성 | PR별 미리보기 주소가 생성되지 않는다 (초심자 가이드 5장) |
| **Cloudflare Access** | 미적용 | `hr.abyz-lab.work` 가 인증 없이 열려 있다 |
| `hr-dev` DNS 레코드 | 미사용 상태로 잔존 | 정리 필요 |
| 터널 이름 | `hr-portal-dev` | 대시보드에서 변경 가능 |

### 브랜치 보호 설정 방법

**Settings → Branches → Add branch protection rule** (또는 Rules → Rulesets)

| 항목 | 설정 |
|---|---|
| Branch name pattern | `main` |
| Require a pull request before merging | 체크 |
| Require status checks to pass | 체크 → `코드 검사`, `사이트 동작 확인` 선택 |

`release` 브랜치도 보호해 사람이 직접 push하지 못하게 막는 것이 좋다.
(CI가 쓰기 때문에 `Allow specified actors to bypass` 에서 GitHub Actions를 허용해야 한다)

> 플랜에 따라 Private 저장소에서 이 기능이 제한될 수 있다. Settings에서 실제 사용 가능한지 확인한다.

---

## 10. 주의사항

- **`docker-compose.yml` 의 포트 바인딩을 `0.0.0.0` 으로 바꾸지 않는다** — 사내망에 그대로 노출된다
- **`public/` 밖의 파일을 `public/` 안으로 옮기지 않는다** — 인터넷에 공개된다
- **`voc-mr-ecr` 관련 설정을 건드리지 않는다** — 별도 운영 서비스다
- **호스트 nginx 설정 변경 시 반드시 `sudo nginx -t` 로 검증 후 `reload`** — `restart` 는 순단이 발생한다
- **실제 직원 개인정보를 넣지 않는다** — 인증이 적용되기 전까지
