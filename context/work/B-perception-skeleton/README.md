# 트랙 B — 스켈레톤 인식 + 구도 정렬 점수

## 목표
C270 프레임에서 경량 pose(스켈레톤) 모델로 키포인트를 뽑고, 구도 템플릿 1개를 기준으로
"지금 사람이 그 구도에 얼마나 잘 맞는지"를 0~1 정렬 점수로 계산한다.

## 왜 지금 이걸 할 수 있나
로봇도, iPad도, 폰도 필요 없다. C270(또는 그냥 노트북 웹캠)과 파이썬만 있으면 지금 바로
시작할 수 있는 유일한 트랙 — Ubuntu 환경 세팅을 기다릴 필요도 없이 이 부분부터 먼저 짤 수 있다.

## 현재 상태
`진행 중` — 최소 인식(사람 접근/정위치 신호)까지 구현·검증 완료. 구도 템플릿 정렬 점수·AR 오버레이는 다음.

구현된 것 (`src/geekseek/perception.py`):
- `MediaPipePersonSensor` — MediaPipe Pose Landmarker(`models/pose_landmarker_lite.task`, IMAGE 모드)로
  프레임 한 장에서 `PersonSignal(detected, size_ratio, center_x, center_y)`을 뽑음. 신뢰도 낮은 관절점은
  걸러내고(`visibility` 기준) 좌표를 [0,1]로 clamp — 처음엔 사람이 없는데도 낮은 신뢰도 관절이 화면 밖으로
  튀어서 size_ratio가 1.8까지 나오는 버그가 있었고, 이 필터로 해결.
- `is_approaching()` / `is_positioned()` — 시나리오 2단계(접근 감지)·4→5단계(정위치 확인)에 대응하는
  임계값 판단 헬퍼. **2026-08-07에 `Coordinator._sense_loop()`로 연결 완료** — `waiting` 상태에서
  `is_approaching()`이 참이면 `PERSON_APPROACHED`, `guiding` 상태에서 `is_positioned()`가 참이면
  `POSITION_REACHED`를 코디네이터가 직접 emit. `config.runtime.person_sensor: mediapipe`로 켜짐.
- `WebcamFrameSource` — cv2 웹캠 읽기가 블로킹이라 별도 스레드에서 계속 최신 프레임만 들고 있다가
  비동기 sense 루프가 호출할 때 넘겨주는 어댑터. `app.py`가 `person_sensor: mediapipe`일 때 같이 만듦.
- `FakePersonSensor` — 다른 트랙과 동일한 fake/real 분리 패턴, 테스트·dev용.
- `scripts/test_webcam_perception.py` — 노트북 웹캠(C270 대신)으로 라이브 검증하는 독립 스크립트.
  실행 결과: 145프레임 전부 정상 감지, size_ratio 0.13~0.22·center 안정적으로 확인(2026-08-06).
- `scripts/live_webcam_pose.py` — 스켈레톤 오버레이를 실시간 `cv2.imshow` 창으로 띄우는 스크립트(2026-08-07).

## 다음 단계
1. ~~OpenCV/MediaPipe로 프레임에서 사람 신호 뽑기.~~ 완료 — 위 참고.
2. ~~경량 pose 모델 선정.~~ MediaPipe Pose Landmarker(lite)로 확정. 모델 파일은 `models/`에 커밋됨(오프라인
   대비, STEP/STL과 같은 방식).
3. 구도 템플릿 스키마 정의 — 예:
   ```python
   TEMPLATE = {
       "id": "frame.upper_body",
       "targets": [
           {"keypoint": "nose",       "zone": (0.45, 0.20, 0.55, 0.35)},  # x0,y0,x1,y1 (정규화 좌표)
           {"keypoint": "left_wrist", "zone": (0.30, 0.55, 0.45, 0.70)},
       ],
   }
   ```
4. 정렬 점수 함수: 각 목표 키포인트가 목표 zone 중심에서 얼마나 벗어났는지 거리로 계산해 0~1로 정규화,
   가중 평균. (`../../knowledge/architecture/camera-robot-architecture.html` §8 파이프라인 요약 참고)
5. 디버그 시각화: 스켈레톤 + 목표 zone 박스 + 정렬 화살표를 프레임에 그려서 창에 띄우기 — 이게 그대로
   나중에 D트랙(iPad2 AR 가이드 스트림)에 재사용됨.

## 완료 기준
- 웹캠 앞에서 실시간(≥10fps)으로 스켈레톤이 그려짐.
- 템플릿 1개에 대해 정렬 점수가 사람이 자세를 맞춰갈수록 올라가는 걸 눈으로 확인.
- 오버레이 합성 함수가 독립 함수로 분리돼 있어(프레임 → 합성 프레임), D트랙에서 그대로 가져다 쓸 수 있음.

## 다음 단계(완료 후, P1~P2)
- 여러 템플릿 지원(전신/상반신/제품 클로즈업) + iPad1에서 고른 템플릿과 연동.
- "제품을 들었는가" 판단(손 키포인트 근접도 우선, 필요시 객체 탐지 추가 — `../../knowledge/decisions.md` 열린 항목).
- SAM2/MobileSAM으로 정렬 완료 시 1회 최종 검증(실루엣 깔끔한지, 다른 사람 안 끼어들었는지).
- VLM 없는 전체 흐름을 먼저 완성한 뒤, `Verifier` 인터페이스에 선택적 VLM 최종 검증을 연결(표정·전체
  구도·어색한 자세). 실시간 루프가 아니라 정렬 완료 후보 프레임 1장에만 호출하며 실패 시 로컬 결과로 진행.

## 참고
- `../../knowledge/architecture/camera-robot-architecture.html` §8, §9

## 진행 기록
"현재 상태"를 바꿀 정도의 진전이 있으면 `../../progress/log/`에 날짜별로 한 줄 남기고,
`../../progress/STATUS.md`의 이 트랙 행도 같이 갱신하세요. 세부 메모는 이 폴더의 `NOTES.md`에.
