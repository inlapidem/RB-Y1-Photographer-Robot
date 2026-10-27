# 명령어 모음

## 설치

```bash
python3 -m pip install -e '.[dev]'
```

## 서버 실행

```bash
python3 -m geekseek --config config/<설정파일>
```

| 설정 파일 | 용도 |
|---|---|
| `config/dev.yaml` | 기본 개발용 — robot/capture/person_sensor 전부 fake, http:8000 |
| `config/local-demo-no-robot.yaml` | 웹캠 인식 + iPad UI만, 로봇 없이 — https:8443 |
| `config/local-demo.yaml` | RViz 로봇 포함 전체 로컬 데모 — https:8443 |
| `config/jetson-phorce.yaml` | Jetson 실기 (phorce 로봇 연동) — https:8443 |

접속 주소: `http(s)://<서버 IP>:<port>/face`(iPad1), `/guide`(iPad2), `/debug`. iPad는 서버와 같은
Wi-Fi 대역에 있어야 한다.

`https:8443` 설정은 `certs/`에 인증서가 없으면 시작이 실패한다:

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -keyout certs/key.pem -out certs/cert.pem \
  -days 3650 -nodes -subj "/CN=geekseek"
```

## Jetson(phorce)에서 GPU로 실행

```bash
source ~/miniforge3/etc/profile.d/conda.sh
conda run --no-capture-output -n geekseek python -m geekseek --config config/local-demo-no-robot.yaml
```

로그에 `delegate=gpu`가 찍히면 정상. GPU wheel 빌드/환경 구성은
[`mediapipe-gpu-build.md`](mediapipe-gpu-build.md) 참고.

## 촬영 모션 슬롯 만들기 (phorce)

`calibration/trial_02.csv`에 측정해 둔 관절 각도로 전신/상반신 촬영 궤적을 만들어
SD카드 슬롯 4·5에 넣는다. 손교시 없이 P-Vector를 직접 계산한다.

```bash
# 1) 로컬에 생성 (calibration/slots/) — 순서 최적화·스케줄까지 출력
python3 scripts/build_motion_slots.py

# 2) 확인 후 SD카드로
python3 scripts/build_motion_slots.py --out /media/phorce/9016-4EF8/Motions
```

슬롯 하나는 28초다. `phorce play`의 기본 대기 시간이 정확히 30초라, 30초로 만들면
물리적으로는 완주하고도 결과가 timeout으로 떨어진다.

**SD에 쓴 뒤에는 반드시 언마운트하고 PCM 전원을 껐다 켜야** PCM이 새 파일을 읽는다.
반영 여부는 `phorce_monitor` 기동 로그의 `적재 슬롯 마스크`로 확인한다
(슬롯 1~3만 있으면 `0x0E`, 4·5까지 있으면 `0x3E`).

## 실기 로봇 스택 (Jetson)

```bash
# 터미널 1 — 로봇 전원을 먼저 켠 뒤
source /opt/ros/humble/setup.bash
ros2 run agx_phorce_bridge phorce_monitor \
  --ros-args -p nic:=eno1 -p mode:=command -p axes:=2 -p mbx_enabled:=true

# 터미널 2
source /opt/ros/humble/setup.bash
ros2 run agx_motion_slot motion_action_server --ros-args -p backend:=ecat
```

`axes`는 **모드마다 다르다.** `command`(재생)는 PCM powered profile이 `axes:=2`만
허용하고 다른 값은 기동 자체가 거부된다:

```
[FATAL] command 모드 거부: 현재 pcm powered profile은 axes:=2만 허용합니다
```

`safe_op`(측정)에서는 `axes:=5`를 쓴다 — `tool_command.md`의 pose 측정 절차가 그것이다.

```bash
phorce doctor     # 준비 상태 진단
phorce list       # 적재된 슬롯 확인
phorce play 4     # 전신 (28초)
phorce play 5     # 상반신 (28초)
```

발사한 모션은 중간에 멈출 수 없다. 정지 수단은 E-Stop 물리 버튼뿐이다.

## 테스트

```bash
pytest tests/
```
