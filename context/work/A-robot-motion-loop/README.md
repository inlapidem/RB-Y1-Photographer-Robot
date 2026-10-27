# 트랙 A — 로봇 최소 왕복 (P0 게이트)

## 목표
phorce Studio로 모션 슬롯 3~4개(`home`, `frame.full_body`, `frame.upper_body`, `safe.retreat`)를
교시하고, Jetson 코드에서 `robot.play(id)`가 시뮬레이터와 실물 양쪽에서 왕복되는 걸 확인한다.

## 왜 이게 먼저인가
이게 안 되면 인식·AR가이드·키오스크가 다 완성돼도 로봇은 안 움직인다. 다른 트랙들이 최종적으로
꽂히는 지점이 이 하나뿐인 통로(`play()`)라서, 여기가 막히면 전체가 막힌다.

## 현재 상태
`시작 전` — 아직 슬롯 교시도, 코드도 없음.

## 다음 단계
1. `phorce doctor` → `ros2 topic hz /phorce/feedback` → `phorce list`로 시스템이 살아있는지 확인
   (`../../knowledge/reference/sdk-docs/01-quickstart.html` §4).
2. 영점 버튼(1번, 0.6초) 눌러서 모션 수신 상태로 만들기.
3. `phorce-studio` 켜고 ① 설정(이름·축 구성·영점·부팅자세) → ② 교시 순서로 슬롯 3~4개 녹화
   (`../../knowledge/reference/sdk-docs/phorce-studio-hackathon-manual.html` §6~7). **영점을 교시보다 먼저 잡을 것.**
4. Jetson 파이썬에서 `import phorce; robot.play(id)`로 각 슬롯이 sim(`--target sim:demo`)에서
   재생되는지, 이어서 실물에서도 재생되는지 확인 (`../../knowledge/reference/sdk-docs/02-tutorial.html` 레슨 2~3).
5. `MotionBusy`/`MotionRejected` 예외 처리 최소 버전 작성 (BUSY만 재시도, 12/13은 사람 개입 안내).
6. `../../knowledge/architecture/camera-robot-architecture.html` §6의 `slot_map.yaml`을 실제 슬롯 번호로
   채워서 저장소에 커밋 (다른 트랙들이 카테고리 이름으로 참조할 파일 — 완성되면 이 폴더의 `NOTES.md`에도
   최종 번호를 적어두면 좋음).

## 완료 기준
- 최소 3개 이상의 이름 붙은 슬롯이 sim과 실물 양쪽에서 `robot.play()`로 재생됨.
- `slot_map.yaml` 파일이 실제 번호로 채워져 저장소에 존재함.
- BUSY 재시도 루프가 매뉴얼 규칙대로 동작(코드 5만 재시도, 12·13은 사람 개입 안내).

## 참고
- `../../knowledge/reference/sdk-docs/01-quickstart.html`, `02-tutorial.html`, `03-manual.html` §8·§9
- `../../knowledge/reference/sdk-docs/phorce-studio-hackathon-manual.html`
- `../../knowledge/reference/sdk-docs/hackathon-addendum-p-vector.pdf` (손교시 대신 정밀 궤적을 코드로 만들고 싶을 때)
- `../../knowledge/architecture/camera-robot-architecture.html` §4, §5, §6, §6-1

## 진행 기록
"현재 상태"를 바꿀 정도의 진전이 있으면 `../../progress/log/`에 날짜별로 한 줄 남기고,
`../../progress/STATUS.md`의 이 트랙 행도 같이 갱신하세요. 작업 중 세부 메모(에러 로그, 시도한 것,
슬롯 번호 등)는 이 폴더의 `NOTES.md`에 자유롭게 쌓으면 됩니다.
