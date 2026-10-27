# work/ — 진행 중인 작업 트랙

지금 돌아가고 있는 작업을 트랙(폴더) 단위로 나눠둔 곳입니다. 새 작업이 생기면 여기에 폴더를
하나 더 만드세요(예: `E-something/`) — 아래 표에도 한 줄 추가.

각 트랙 폴더는 두 파일로 구성됩니다:
- **`README.md`** — 목표·근거·다음 단계·완료 기준. 방향이 바뀌지 않는 한 안정적으로 유지.
- **`NOTES.md`** — 작업하면서 쌓이는 자유 메모(시도한 것, 막힌 것, 참고 링크). 아무나 편하게 추가.

| 트랙 | 폴더 | 한 줄 목표 | 로봇 필요? |
|---|---|---|---|
| 0 | [00-runtime-foundation](00-runtime-foundation/README.md) | 이벤트 상태 머신 + fake 전체 시나리오 공통 기반 | 불필요 — 가장 먼저 시작 |
| A | [A-robot-motion-loop](A-robot-motion-loop/README.md) | 모션 슬롯 3~4개 교시 + `play()` 왕복 확인 | 필요 (sim으로 먼저 가능) |
| B | [B-perception-skeleton](B-perception-skeleton/README.md) | C270 → 스켈레톤 → 구도 정렬 점수 | 불필요 — 지금 바로 시작 가능 |
| C | [C-eef-camera-shutter](C-eef-camera-shutter/README.md) | 엔드이펙터 폰 원격 셔터 왕복 확인 | 폰만 있으면 됨 |
| D | [D-kiosk-web-server](D-kiosk-web-server/README.md) | 로컬 웹서버 + iPad Safari 풀스크린 연결 | iPad만 있으면 됨 |

트랙 0은 B·C·D가 공유하는 상태·이벤트·실행 뼈대입니다. 먼저 최소 기반과 fake 전체 시나리오를 만들고,
B·C·D는 같은 인터페이스에 실제 구현을 연결합니다. A는 `FakeRobot`을 phorce 구현으로 교체하는 방식으로
나중에 통합할 수 있습니다. 물리 리스크 우선순위는 여전히 A와 C가 높지만, Jetson·로봇 없이 가능한 작업은
**0 → D → B**, 실제 폰이 있으면 C를 병행하는 순서가 적합합니다. 구현 기준은
[`../knowledge/architecture/runtime-architecture.md`](../knowledge/architecture/runtime-architecture.md)를 따릅니다.

## 작업 시작·종료 시 체크리스트

- **시작할 때**: 해당 트랙 `README.md`의 "현재 상태"를 `진행 중`으로 바꾸고, 오늘 날짜로
  [`../progress/log/`](../progress/log/)에 짧게 한 줄 남기기(누가, 뭘 시작하는지).
- **끝나거나 막혔을 때**: `README.md`의 "현재 상태"·"다음 단계" 갱신, `NOTES.md`에 상세 기록,
  [`../progress/STATUS.md`](../progress/STATUS.md)의 해당 트랙 행 갱신, 새로 확정된 게 있으면
  [`../knowledge/decisions.md`](../knowledge/decisions.md)에 반영.

결정된 것/열려 있는 것은 [`../knowledge/decisions.md`](../knowledge/decisions.md)에서 확인하세요.
