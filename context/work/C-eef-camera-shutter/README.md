# 트랙 C — 엔드이펙터 폰 원격 셔터

## 목표
로봇팔 끝에 달린 폰을 Jetson에서 HTTP로 원격 트리거해서 사진을 찍고, JPEG를 Jetson으로 회수한다.

## 왜 오늘 안에 닫아야 하나
팀은 아이폰을 쓰고 싶어 하는데, 아이폰은 안드로이드+IP Webcam 조합만큼 간단하지 않다(iOS가 백그라운드
카메라 접근을 막음). 커스텀 앱이 필요할 수도 있는, 지금까지 결정 중 일정 리스크가 가장 큰 항목이라
가장 먼저 짧게 검증해서 막히면 빨리 대안으로 갈아타야 한다.

## 현재 상태
`완료` — 아이폰 + Safari 웹앱 방식, `CaptureDevice`(`WebAppCapture`)로 실연동까지 끝남. 전체 상태
머신 e2e 검증 완료 (`../../knowledge/decisions.md` 참고).

## 검증한 경로들 (각 30분~1시간 타임박스)

### 경로 1 — 안드로이드 + 기존 앱 (fallback, 가장 안전)
"IP Webcam" 등 기존 무료 앱 설치 → Wi-Fi에서 `GET http://<폰IP>:8080/photo.jpg`로 즉시 고해상도 캡처,
`/video`로 MJPEG 프리뷰. 커스텀 코드 없음. **다른 두 경로가 막히면 이걸로 확정.**

### 경로 2 — 아이폰 커스텀 미니 앱
AVFoundation으로 카메라 캡처 + 가벼운 임베디드 HTTP 서버(예: Swift `Swifter` 라이브러리, 파일 하나로
붙임) 얹어서 안드로이드 경로와 같은 API 모양(`/shot.jpg`, `/video`)을 노출. 팀에 iOS/Swift 경험자가
있으면 반나절 안에 될 수 있음.

### 경로 3 — 아이폰 Shortcuts + Pushcut 웹훅
Jetson이 Pushcut 웹훅 URL을 호출 → 아이폰에 무음 푸시 → Shortcuts 자동화가 "사진 찍기" 액션 실행 →
결과 이미지를 `POST`로 Jetson 엔드포인트에 업로드. 네이티브 코드 불필요하지만, 백그라운드 신뢰성과
지연시간(1~2초)이 불확실 — 반드시 여러 번 반복 테스트해서 안정적인지 확인할 것.
시도해봤지만 화질이 기대에 못 미치고 트리거마다 알림 UX가 거슬려서 채택 안 함.

### 경로 4 — 아이폰 Safari 웹앱 (채택)
원래 세 경로에는 없던 네 번째 방식. 폰이 Jetson이 서빙하는 HTTPS 페이지를 Safari로 열고
`getUserMedia`로 카메라 스트림을 계속 붙잡고 있음(사람이 매번 셔터를 누를 필요 없음). Jetson은
WebSocket으로 그 폰에 붙어 있다가, 원할 때 `"capture"` 텍스트 메시지 한 번으로 무음 원격 트리거 →
폰이 그 순간 프레임을 캔버스로 캡처해 JPEG Blob으로 같은 소켓에 되돌려줌. 네이티브 앱도, 외부
서비스(Pushcut)도 필요 없고 D트랙에서 이미 쓰던 FastAPI 패턴을 그대로 재사용. iOS가 카메라 API를
HTTPS(또는 localhost)에서만 허용해서 자체서명 인증서가 한 번 필요함(폰에서 "이 웹사이트 방문" 수락).

## 다음 단계
1. ~~경로 1(안드로이드)을 먼저 30분 안에 되게 만들어서 baseline 확보.~~
2. ~~경로 2~4를 짧게 찔러보고 가장 안정적인 걸 선택.~~ → 경로 4(웹앱) 채택.
3. ~~결정되면 `../../knowledge/decisions.md`를 갱신하고 확정 표로 옮기기.~~
4. ~~최종 API 모양을 이 파일 하단에 기록.~~
5. ~~`scripts/phone_capture_server.py`의 로직을 `CaptureDevice` 프로토콜에 맞는 실제 구현체로 옮겨
   `Coordinator`에 연결.~~ `geekseek.capture.WebAppCapture` + `web.py`의 `/phone`, `/phone-ws` 라우트로
   완료. `config/local-demo.yaml`(`runtime.capture: webapp`, TLS는 `certs/`)로 켤 수 있음. 구도 선택 →
   RViz 로봇 이동 → 정렬 → **실제 아이폰 촬영** → 리뷰까지 전체 상태 머신 e2e 검증됨(2026-08-06).

## 완료 기준
- ~~Jetson에서 HTTP 요청 한 번 → 폰이 셔터를 누르고 → JPEG가 Jetson에 저장됨. 3회 연속 성공.~~
  달성 — 연속 3회 성공 + 15초 내 30회 연속 트리거(약 2Hz)도 파일 충돌·유실 없이 전부 성공(2026-08-06).
- ~~최종 API 스펙(엔드포인트 경로·메서드·응답)이 이 파일에 기록됨.~~ 아래 참고.

## 최종 API 스펙
방식: 아이폰 Safari 웹앱(경로 4) — HTTPS 상시 연결 + 서버발 원격 트리거.
구현: `scripts/phone_capture_server.py` (서버), `web/phone_capture.html` (폰 페이지).

```
GET  /            폰이 여는 페이지. getUserMedia로 카메라 스트림 확보 후 wss:///ws 로 연결.

WS   /ws          폰이 연결을 유지하는 소켓.
                   서버 → 폰: 텍스트 "capture"  (트리거)
                   폰 → 서버: JPEG bytes         (캡처된 프레임)
                   서버는 받은 즉시 photos/phone_<YYYYmmdd_HHMMSS_ffffff>.jpg 로 저장.

POST /trigger      연결된 폰에 "capture"를 보내는 트리거. 응답:
                   {"status": "ok"}
                   {"status": "error", "detail": "no phone connected"}  (폰 미연결 시)
```

필수 조건: HTTPS만 카메라 API 허용(iOS 제약) → `certs/`에 자체서명 인증서 필요, 폰에서 최초
접속 시 "이 웹사이트 방문" 1회 수락 필요. 평문 HTTP로는 카메라 권한 프롬프트 자체가 안 뜸.

## 참고
- `../../knowledge/architecture/camera-robot-architecture.html` §2, §3(시스템 다이어그램의 "캡처 브릿지"), §10

## 진행 기록
"현재 상태"를 바꿀 정도의 진전이 있으면 `../../progress/log/`에 날짜별로 한 줄 남기고,
`../../progress/STATUS.md`의 이 트랙 행도 같이 갱신하세요. 세부 메모는 이 폴더의 `NOTES.md`에.
