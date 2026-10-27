# 현재 상태 (한눈에)

> 이 파일은 **지금 스냅샷**입니다 — 과거 기록이 아니라 최신 상태로 계속 덮어써서 유지하세요.
> 시간 흐름에 따른 기록은 [`log/`](log/)에 날짜별로 남깁니다.

**마지막 갱신**: 2026-08-07

## 요약
물리 구성(C270=인식 전용, iPad1=구도선택 UI, iPad2=AR가이드+키오스크, iPad↔Jetson=로컬 웹서버) 확정.
2026-08-07에 `workflow.py`를 `../knowledge/scenario-walkthrough.md`의 사용자 관점 8단계
(waiting/greeting/deciding/guiding/capturing/previewing/asking/farewell)로 전면 재작성했다 — 예전
BOOTING/READY/REPOSITIONING/GUIDING/VERIFYING/CAPTURING/REVIEWING 구조는 폐기. VLM 정렬 검증(`Verifier`)은
제거하고, B트랙 `MediaPipePersonSensor`를 코디네이터 자체 감지 루프(`_sense_loop`)로 직접 연결해
사람 접근(`waiting→greeting`)·정위치(`guiding→capturing`)를 자동 판단하게 했다. 로봇은 5단계
(버스트 촬영)에서만 실제로 움직이므로, `robot: fake`로 설정하면 RViz 없이도 1~4단계(웹캠 감지 +
iPad UI)를 노트북 하나로 검증할 수 있다(`config/local-demo-no-robot.yaml`). 엔드이펙터 폰 연동(아이폰
Safari 웹앱, `WebAppCapture`)은 새 상태 머신에도 그대로 재사용.
프런트엔드는 Codex가 `web/face-mock.html`/`guide-mock.html`(8단계 비주얼 목업)을 완성하고 실제
SSE(`/events`)·새 API에 연결까지 끝냈다 — 실제 서버로 8단계 전부 + 에러 상태 + `?debug=1`까지
렌더링해서 검증 완료. 이어서 3가지를 추가했다: (1) `/debug/webcam` — 웹캠 인식(스켈레톤+상태값)을
실시간으로 볼 수 있는 디버그 MJPEG 스트림, (2) `/live/camera` — 디버그 오버레이 없는 깨끗한
셀피미러 웹캠 스트림, `guide-mock.html`의 4·5단계 카메라 프리뷰 자리에 실제로 꽂아넣음, (3)
`vlm.py`의 `ClaudeGreeter` — 2단계(greeting) 진입 시점 웹캠 프레임으로 Claude(`claude-opus-5`,
thinking 끔·effort low)를 백그라운드 호출해 개인화 인사말(`context.greeting_line`)을 만듦, 실패해도
기본 문구로 자연스럽게 폴백(`runtime.vlm_enabled: false`가 기본, 키 필요). Codex는 지금 음성(TTS,
Web Speech API, iPad1 전용) 작업 중 — 끝나면 `greeting_line`을 mock.js 캡션에 반영하는 작업이 남음.
`pytest tests/`(30개) 전부 통과.

## 트랙별 상태

| 트랙 | 상태 | 비고 |
|---|---|---|
| [0. 런타임 공통 기반](../work/00-runtime-foundation/README.md) | 🟡 진행 중 | 코어·STEP 기반 RViz·iPad fake 흐름 완료, 영상 연결이 다음 |
| [A. 로봇 최소 왕복](../work/A-robot-motion-loop/README.md) | 🔴 시작 전 | P0 게이트 — 가장 먼저 되어야 함 |
| [B. 스켈레톤 인식](../work/B-perception-skeleton/README.md) | 🟡 진행 중 | 접근/정위치 감지를 워크플로 코디네이터에 실연결 완료. 구도 템플릿 정렬 점수는 다음 |
| [C. 엔드이펙터 폰 셔터](../work/C-eef-camera-shutter/README.md) | 🟢 완료 | `WebAppCapture`로 `CaptureDevice` 실연동 완료. 새 8단계 상태 머신에도 그대로 재사용 확인 |
| [D. 키오스크 웹서버](../work/D-kiosk-web-server/README.md) | 🟡 진행 중 | 백엔드 8단계 상태 머신 완료, 프런트(face-mock/guide-mock) SSE 실연동은 Codex 작업 중 |

(🔴 시작 전 · 🟡 진행 중 · 🟢 완료 · ⚠️ 막힘)

## 지금 막혀 있는 것
없음.

## 다음 체크인 때 확인할 것
- Codex 음성(TTS) 작업 완료 — `context.greeting_line`을 mock.js 캡션에 반영(2단계만, 없으면 기본
  캡션 유지)하는 마무리 작업 필요
- 2단계 인사말 VLM: 지금 `ClaudeGreeter`(Anthropic API)는 파이프라인 검증용 프로토타입일 뿐 — 실제
  타겟은 로컬 VLM(Jetson, 현장 Wi-Fi 비의존). Jetson 접근되면 `Greeter` 프로토콜에 맞춰
  `LocalVlmGreeter` 만들어서 교체할 것 (`../knowledge/decisions.md` 참고)
- 실제 iPad 2대에서 `/face`, `/guide`, SSE 동기화를 확인할 것
- 트랙 A(로봇 실물, phorce) — CAD 출력 대기 중이라 보류. 나올 때까지는 RViz 시뮬레이션까지만
- 구도별 정렬 세분화(지금은 "화면 중앙" 하나로만 판단)
