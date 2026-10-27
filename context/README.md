# context/ — 팀·에이전트 작업 인계 폴더

이 저장소를 새로 열어보는 사람(또는 다른 에이전트)이 대화 기록 없이도 지금까지의 논의를 따라잡고,
바로 다음 작업을 이어갈 수 있게 만든 폴더입니다. **처음이면 이 순서로 읽으세요.**

1. [`progress/STATUS.md`](progress/STATUS.md) — 지금 어디까지 됐는지 한눈에
2. [`knowledge/decisions.md`](knowledge/decisions.md) — 확정된 것 / 아직 열려 있는 것
3. [`knowledge/architecture/camera-robot-architecture.html`](knowledge/architecture/camera-robot-architecture.html) — 전체 소프트웨어 아키텍처 설계 (브라우저로 열어서 볼 것)
4. [`knowledge/architecture/runtime-architecture.md`](knowledge/architecture/runtime-architecture.md) — 단순한 상태 머신·모듈·ROS 경계 구현 기준
5. [`work/README.md`](work/README.md) — 지금 진행 중인 작업 트랙들, 여기서 하나 골라 시작

## 폴더는 왜 이렇게 셋으로 나뉘어 있나

여러 사람·여러 에이전트가 번갈아 작업할 걸 전제로 나눴습니다. 각 폴더는 **갱신 빈도와 성격이
다릅니다** — 섞이면 오래된 정보와 최신 정보가 구분이 안 돼서 나뉜 겁니다.

| 폴더 | 성격 | 언제 손대나 |
|---|---|---|
| [`knowledge/`](knowledge/) | 안정적인 지식 — 참고 문서, 아키텍처 설계, 확정된 결정 | 설계가 바뀌거나 새 결정이 날 때만 |
| [`work/`](work/) | 지금 진행 중인 작업 트랙 (트랙별 폴더) | 작업을 시작·진행·완료할 때마다 |
| [`progress/`](progress/) | 시간순 기록(`log/`) + 현재 스냅샷(`STATUS.md`) | 세션이 끝날 때마다, 또는 의미 있는 진전이 있을 때 |

## 프로젝트 한 줄 요약

Roboticus 로봇 해커톤(2026-08-05~08, KAIST) 참가 프로젝트. phorce SDK(Jetson–pcm–phact 로봇팔) +
iPad 2대 + 엔드이펙터 카메라폰 + C270 웹캠으로 구성된, "핫플레이스"에서 좋은 구도를 골라 자동으로
사진을 찍어주는 로봇. 원래 제안서는 임피던스 제어·직접교시를 실시간 코드로 구현하는 걸 전제했지만,
실제 해커톤 SDK는 참가자 코드에 **① `/phorce/feedback` 읽기(1kHz)와 ② 미리 저장된 모션 슬롯(1~50)
재생, 이 두 가지만** 허용합니다. 이 제약을 반영해 다시 짠 게 `knowledge/architecture/` 문서입니다.

## 환경 메모 (Windows → Ubuntu 이어가기)

이 설계는 Windows에서 정리했고, 실제 코드 작업은 Jetson이 있는 **Ubuntu 환경**에서 이어집니다.
- phorce Python 파사드(`import phorce`)와 CLI(`phorce doctor/list/play/status`)는 Jetson에 이미
  설치돼 있습니다 (`knowledge/reference/sdk-docs/01-quickstart.html` 참고).
- ROS 2 기반이므로 `qos_profile_sensor_data`로 피드백을 구독해야 합니다
  (`knowledge/reference/sdk-docs/03-manual.html` §6, §11 흔한 실수).
- CAD 기준 원본은 `assets/cad/Assemble_CAM.step`, 재현 가능한 STL 생성기는
  `tools/extract_step_links.py`, RViz용 생성 메시와 manifest는 `ros/geekseek_fake_robot/meshes/`에 있습니다.
- **실제 소스 코드는 이 `context/` 폴더가 아니라 저장소 루트에** 둡니다(예: `src/`, `jetson/` 등,
  트랙 작업이 시작되면 새로 만들면 됨). `context/`는 지식·조율 계층이지 코드 저장소가 아닙니다.

## 작업 흐름 (요약)

1. `work/`에서 트랙 하나를 고른다 (또는 새 작업이면 `work/`에 폴더를 새로 만든다).
2. 해당 트랙 `README.md`의 "현재 상태"를 갱신하며 작업한다. 세부 메모는 그 폴더의 `NOTES.md`에.
3. 세션이 끝나거나 진전이 있으면 `progress/log/YYYY-MM-DD.md`에 기록하고 `progress/STATUS.md`를
   최신 스냅샷으로 갱신한다.
4. 새로 확정된 결정이 있으면 `knowledge/decisions.md`에 반영한다.

## 폴더 구조

```
context/
├── README.md                        ← 지금 이 파일
├── knowledge/                       ← 안정적 지식
│   ├── decisions.md                    확정/미확정 결정 로그
│   ├── architecture/
│   │   ├── camera-robot-architecture.html   전체 아키텍처 설계 문서(다이어그램 포함)
│   │   └── runtime-architecture.md          상태 머신·모듈·실행 구조 구현 기준
│   └── reference/sdk-docs/             해커톤 운영진이 배포한 SDK 문서 원본
│       ├── 01-quickstart.html
│       ├── 02-tutorial.html
│       ├── 03-manual.html
│       ├── phorce-studio-hackathon-manual.html
│       ├── pcm-board-guide.html
│       ├── hackathon-addendum-p-vector.pdf   (원본명: 해커톤_추가공지_p_vector.pdf)
│       └── ot-hackathon-kickoff.pdf          (원본명: OT_해커톤시작_2026-08-05_최종본)
├── work/                            ← 진행 중인 작업 트랙
│   ├── README.md                       트랙 목록 + 작업 체크리스트
│   ├── 00-runtime-foundation/{README.md, NOTES.md}
│   ├── A-robot-motion-loop/{README.md, NOTES.md}
│   ├── B-perception-skeleton/{README.md, NOTES.md}
│   ├── C-eef-camera-shutter/{README.md, NOTES.md}
│   └── D-kiosk-web-server/{README.md, NOTES.md}
└── progress/                        ← 시간순 기록 + 현재 스냅샷
    ├── STATUS.md                       지금 상태 한 장(계속 덮어씀)
    └── log/
        ├── README.md                    로그 작성 규칙
        └── 2026-08-06.md                날짜별 세션 기록
```
