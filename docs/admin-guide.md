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

### 수동 배포

자동 배포가 구성되기 전까지는 수동으로 반영한다.

```
cd ~/workspace/github-hnabyz-bot/hr-portal && git pull
```

바인드 마운트 방식이라 **컨테이너 재시작이 필요 없다.** `git pull` 즉시 반영된다.

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

## 7. 접근 권한 관리

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

## 8. 미완료 항목

아래는 아직 구성되지 않았다. 진행 상황은 [README](../README.md)의 로드맵을 참고한다.

| 항목 | 현재 상태 | 영향 |
|---|---|---|
| **Cloudflare Access** | 미적용 | `hr.abyz-lab.work` 가 인증 없이 열려 있다 |
| **자동 배포** | 미구성 | 병합 후 관리자가 `git pull` 을 직접 해야 한다 |
| **자동 검사(CI)** | 미구성 | PR 검증이 수동이다 |
| **미리보기 환경** | 미구성 | PR별 미리보기 주소가 생성되지 않는다 |
| `hr-dev` DNS 레코드 | 미사용 상태로 잔존 | 정리 필요 |
| 터널 이름 | `hr-portal-dev` | 대시보드에서 변경 가능 |

> **초심자 가이드는 위 4가지가 구성된 것을 전제로 작성되어 있다.**
> 작업자를 합류시키기 전에 먼저 구성해야 한다.

---

## 9. 주의사항

- **`docker-compose.yml` 의 포트 바인딩을 `0.0.0.0` 으로 바꾸지 않는다** — 사내망에 그대로 노출된다
- **`public/` 밖의 파일을 `public/` 안으로 옮기지 않는다** — 인터넷에 공개된다
- **`voc-mr-ecr` 관련 설정을 건드리지 않는다** — 별도 운영 서비스다
- **호스트 nginx 설정 변경 시 반드시 `sudo nginx -t` 로 검증 후 `reload`** — `restart` 는 순단이 발생한다
- **실제 직원 개인정보를 넣지 않는다** — 인증이 적용되기 전까지
