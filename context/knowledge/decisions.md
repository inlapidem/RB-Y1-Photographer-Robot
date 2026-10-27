# 결정 로그

`architecture/camera-robot-architecture.html`의 §2·§10과 팀 논의를 압축한 표. 새 결정이 생기면
여기에 한 줄씩 추가하세요 — 아키텍처 문서 전체를 다시 읽지 않아도 최신 상태를 알 수 있게.

## 확정된 것

| 항목 | 결정 | 근거 |
|---|---|---|
| 참가자 코드가 로봇팔에 할 수 있는 일 | `/phorce/feedback` 읽기(1kHz) + `play(모션ID 1~50)` 재생, 이 두 가지뿐 | `03-manual.html` §1, §12 |
| 직접교시 방식 | 실시간 코드 아님 — phorce Studio GUI로 서보 끄고 손으로 가르쳐 SD카드에 슬롯 저장 | `phorce-studio-hackathon-manual.html` §7 |
| 정밀 카메라 무빙(팬/틸트) | `MotionMap.csv`의 P-Vector `[yd, Ltraj, s0, sd]`를 코드로 직접 생성해서 대체 가능 | `hackathon-addendum-p-vector.pdf` |
| C270 웹캠 역할 | 실시간 인식 전용 센서(사람·포즈·제품 유무). 촬영에는 관여 안 함 | 팀 확인 |
| iPad 1 (가로, 상단) | 얼굴/상태 UI + 구도 템플릿 선택(터치 추가) | 팀 확인 |
| iPad 2 (세로, 하단) | 라이브 프리뷰 + AR 가이드(정렬 안내) + 재촬영 등 키오스크 조작 | 팀 확인 |
| iPad ↔ Jetson 연동 | 네이티브 iOS 앱 없이, Jetson 로컬 웹서버(FastAPI) + iPad Safari 풀스크린(가이드 접근 모드) | 팀 확인 |
| 로봇팔 ↔ iPad 스탠드 | 물리적으로 분리된 별개 장치. 스탠드=고정 감독의 눈, 팔=구도로 움직여 찍는 손 | 팀 확인 |
| 피사체 추적 방식 | 연속 서보잉 대신 이산적 재구도(카테고리 바뀔 때만 슬롯 재생) + 사람이 AR 가이드 보고 스스로 위치 조정 | SDK 제약 + 팀 확인 |
| AR 가이드 구현 방식 | ARKit 네이티브 앱 안 씀 — Jetson이 OpenCV로 스켈레톤+그리드를 프레임에 합성해 MJPEG로 스트리밍 | 팀 확인 |
| 무거운 분할 모델(SAM2/MobileSAM) | 매 프레임 아님 — 정렬 점수 임계값 통과 시 1회 최종 검증 게이트로만 | 팀 확인 |
| 접촉/이상 감지 | `current_a`/`dob_a` 모니터링으로 감지까지만 가능(실시간 순응 불가) → 안전 슬롯 재생으로 대응 | SDK 제약 |
| 접촉 이상 감지 우선순위 | P2로 하향 — 지금은 안 함 | 팀 결정(2026-08-06) |
| ASAP 잔차보정 / 연속 시각서보잉 | 실물에서 이번 대회 범위 밖 — 컨셉 슬라이드로만 | SDK 저수준 접근 불가 |
| 런타임 오케스트레이션 | 단일 Python 프로세스 + `asyncio.Queue` 기반 이벤트 상태 머신. 사용자는 명령 하나로 실행 | 팀 결정(2026-08-06) |
| GaP 판단 실행 방식 | 전체를 2Hz 폴링하지 않고 이벤트 발생 즉시 전이. 2Hz/타이머는 BUSY 재시도·안전 확인에만 사용 | 반응성·단순성 확보(2026-08-06) |
| 상태 관리 | `Coordinator`만 `WorkflowContext`를 변경하는 단일 작성자 구조 | 레이스 방지·테스트 용이성(2026-08-06) |
| ROS 2 사용 범위 | phorce 모션/피드백 경계에서만 사용. Pose·웹·폰·상태 머신은 일반 Python | 로봇 SDK 격리(2026-08-06) |
| 구현 모듈 구조 | 초기에는 `workflow/coordinator/perception/robot/capture/web/config` 중심의 작은 모듈 구성(`verification`은 2026-08-07 워크플로 재작성 때 제거). 실제로 커질 때만 분리 | 과설계 방지(2026-08-06) |
| 하드웨어 교체 방식 | Robot·Capture·Camera/Pose 경계만 작은 인터페이스로 격리하고 fake/실제 구현을 설정으로 선택 | 노트북 선행 개발·확장성(2026-08-06) |
| 개발 프로필 | `dev`에서 fake/녹화 영상으로 전체 흐름 검증 후 `jetson`에서 실제 구현체 교체 | Jetson 전 최대 개발(2026-08-06) |
| 로봇 없는 시각 검증 | STEP XCAF 조립 트리에서 5축 rigid 링크와 고정 키오스크/iPad/C270 메시를 생성하고 RViz fake node로 의미 포즈를 검증 | 로봇 팀 작업과 독립적으로 흐름 개발(2026-08-06) |
| ~~VLM 도입 방식~~ | ~~VLM 없는 `LocalVerifier` 버전을 먼저 완성하고, 같은 `VERIFYING` 상태에 `VlmVerifier`를 선택적으로 연결~~ → 2026-08-07에 폐기, 아래 "워크플로 8단계 재작성" 참고 | 팀 결정(2026-08-06) |
| ~~VLM 실패 정책~~ | ~~정렬 완료 후보 프레임에 1회만 호출하고, 타임아웃·네트워크/API 오류 시 로컬 결과로 진행(`fail_open`)~~ → 2026-08-07에 폐기 | 기본 촬영 경로 보호(2026-08-06) |
| 엔드이펙터 폰 기종/연동 방식 | 아이폰 + Safari 웹앱. 폰이 HTTPS 페이지에서 `getUserMedia`로 카메라 스트림을 상시 유지하고, Jetson이 WebSocket으로 무음 원격 트리거 → 그 순간 프레임을 캡처해 업로드. iOS가 카메라 API를 HTTPS(또는 localhost)에서만 허용해서 자체서명 인증서가 필요(최초 1회 "이 웹사이트 방문" 수락) | Android IP Webcam 대비 화질 우위 확인, 원격 트리거 연속 3회 성공(2026-08-06) |
| 워크플로 8단계 재작성 | `BOOTING/READY/REPOSITIONING/GUIDING/VERIFYING/CAPTURING/REVIEWING`(VLM `Verifier` 포함) 구조를 폐기하고, `../knowledge/scenario-walkthrough.md`의 사용자 관점 8단계(waiting/greeting/deciding/guiding/capturing/previewing/asking/farewell)로 전면 교체. 정렬 확인은 VLM이 아니라 B트랙 웹캠(`is_approaching`/`is_positioned`)이 직접 담당 — 로봇은 5단계(버스트 촬영)에서만 움직이므로 `robot: fake`면 RViz 없이도 1~4단계 검증 가능 | 실제 스토리 우선 설계, 구현 단순화(2026-08-07) |

## 아직 열려 있는 것

| 항목 | 확인 방법 | 담당 트랙 |
|---|---|---|
| Jetson·iPad·폰 동일 네트워크 확보 | 현장 Wi-Fi/핫스팟으로 Safari 풀스크린 접속 테스트 | [work/D-kiosk-web-server](../work/D-kiosk-web-server/README.md) |
| 제품 인식 범위 | 손 키포인트 근접도로 단순화 vs 별도 객체 탐지 — §8 파이프라인 자리잡은 뒤 결정 | [work/B-perception-skeleton](../work/B-perception-skeleton/README.md) |
| 2단계 개인화 인사말 VLM — 최종 모델 | 현재 `vlm.py`의 `ClaudeGreeter`(Anthropic API)는 파이프라인(트리거 타이밍·프롬프트·fail_open·SSE 반영) 검증용 프로토타입. 실제로는 현장 Wi-Fi 의존 피하려고 로컬 VLM(예: Qwen3 MoE 30B대) on-device를 검토 중인데, 이 노트북에서는 못 돌려보고 Jetson에서 직접 테스트해봐야 함. `Greeter` 프로토콜만 맞추면 교체는 `app.py` 한 줄(`ClaudeGreeter()` → `LocalVlmGreeter(...)`) | Jetson 접근 대기 중 |

## 새 결정을 추가할 때
한 줄로: **무엇을 / 왜 / 언제(날짜) / 누가**. 근거가 대화뿐이면 "팀 확인(YYYY-MM-DD)"이라고 적고,
자세한 맥락은 [`../progress/log/`](../progress/log/)의 해당 날짜 항목에 남겨서 나중에 "왜 이렇게
했지?"를 추적할 수 있게 하세요.
