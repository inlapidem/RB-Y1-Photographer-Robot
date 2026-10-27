# RB-Y1 오른팔 이식

GeekSeek의 Phorce 관절값이나 연속 녹화 궤적은 RB-Y1에서 재생하지 않는다. 휴대폰을
오른쪽 그리퍼로 실제로 쥔 상태에서 **11개의 최종 카메라 구도만 티칭**한다. 런타임은
기존 `calibration/rby1/frame.*.json` 경로에서 그 keypose를 읽어 정확한 최단 폐경로를
구한다. 핵심 keypose에서는 0.1초만 정지해 한 장씩 찍고, 나머지는 keypose 사이를
이동하면서 촬영해 총 30장을 만든다. 현재 상반신은 정지 10장+이동 20장, 전신은
정지 11장+이동 19장이다.

## 실행 전 준비

1. 휴대폰 위치가 매번 반복되도록 오른쪽 그리퍼에 스토퍼와 미끄럼 방지 패드를 설치한다.
2. 휴대폰 질량·무게중심, 케이블 여유, 보조 안전 스트랩을 확인한다.
3. `h`로 사람을 피해 복귀할 safe home을 저장하고, `m`으로 촬영 구도 11개를 저장한다.
4. 자동으로 연결하면 위험한 구도 쌍은 JSON의 `planning.blocked_edges`에 넣는다.
5. 시뮬레이터와 사람 없는 25% 속도 실기에서 생성 경로 전체를 검증한다.
6. 전원·서보·Control Manager는 운영자가 Web UI에서 준비하고 서비스는 자동으로 켜지 않는다.

각 keypose에는 재생 정본인 오른팔 7축 `right_arm_rad`, 검증용
`base → ee_right` 변환, 그 자세에서 읽은 오른손 F/T 센서 값이 같이 저장된다.
`tool.ee_camera_transform`은 `ee_right → 휴대폰 카메라 렌즈` 외부 파라미터다. 그리퍼에
휴대폰을 다시 물렸을 때 이 관계가 바뀌면 모든 구도가 같이 틀어진다.

11개 경로는 nearest-neighbour가 아니라 Held–Karp 동적 계획법으로 `home → 11개 → home`
최단 순서를 정확히 구한다. 비용은 오른팔 관절 중 가장 오래 걸리는 축의 이동량이다.
단, 이 최적화는 충돌 검사를 대신하지 않는다. 금지한 두 포즈 사이를 연결하지 않도록
`blocked_edges: [["wp03", "wp08"]]`처럼 기록하고 전체 간선을 사전 검증해야 한다.

## 실행

```bash
python3 -m pip install -e '.[rby1,dev]'
cp config/jetson-rby1.example.yaml config/jetson-rby1.yaml
python3 -m geekseek --config config/jetson-rby1.yaml
```

`Rby1Robot`은 의도적으로 전원/서보를 코드에서 켜지 않는다. `preflight()`는 SDK 연결과
Control Manager의 ENABLE 상태만 검사한다. 실패나 취소 때는 SDK의
`cancel_control()`을 요청하지만, 즉시 물리 정지를 보장하는 수단은 EMO다.
실기 어댑터는 `schema: geekseek.rby1.keyposes/v1`이 없는 과거 연속 녹화 파일도 거부한다.
따라서 기존 경로에 과거 `segments` 파일이 남아 있어도 실수로 재생되지 않는다.

## 11개 휴대폰 keypose 티칭하기

휴대폰을 그리퍼로 완전히 고정한 뒤 SDK Gravity Compensation으로 팔을 직접 움직인다.
연속 궤적은 저장하지 않는다. `h`는 안전 복귀 자세, `m`은 현재 최종 구도를 저장하며
정확히 11개를 기록해야 `q`로 끝낼 수 있다. 기존 파일 경로는 그대로 유지된다.

```bash
python3 scripts/record_rby1_right_arm.py \
  --address 192.168.30.1:50051 \
  --model a \
  --out calibration/rby1/frame.full_body.json \
  --anchor-count 11 \
  --capture-count 30 \
  --grasp-id iphone_fixture_v1
```

`u`는 마지막 키포즈를 취소한다. 완성된 파일은 기존
`rby1_trajectories.frame.full_body` 설정에서 바로 읽힌다. 서비스의 `sweep()` 인터페이스와
상태 머신은 바뀌지 않는다.

## EEF, 그리퍼와 F/T 센서

SDK 0.10의 `RobotState.ft_sensor_right`에서 오른손 6축 힘[N]·토크[Nm]를 읽을 수 있다.
`tool_flange_right`는 별도로 자이로, 가속도, 디지털 I/O, 출력 전압을 제공한다. 키포즈
파일은 티칭 시 F/T 값을 함께 남겨 휴대폰 장착 상태와 자세별 기준값을 분석할 수 있게 한다.

장착된 그리퍼는 `/dev/rby1_gripper`의 Dynamixel 두 축을 current-based position mode로
제어하는 별도 장치다. F/T 센서는 접촉 감시용이고 그리퍼의 파지력을 직접 설정하는 API가
아니다. 그리퍼 전류/위치 제어는 정격, 홈 범위, 휴대폰 허용 압력을 확인한 다음 별도
어댑터로 넣어야 한다. 현재 서비스는 촬영 중 그리퍼를 자동으로 열거나 닫지 않는다.

다음 진단은 힘·토크와 플랜지 상태만 읽고 모터 명령을 보내지 않는다. `--probe-gripper`는
로컬 Dynamixel ID 0/1에 ping과 엔코더 읽기만 추가한다.

```bash
python3 scripts/check_rby1_eef.py --address 192.168.30.1:50051 --probe-gripper
```

단독 미리보기는 먼저 요약만 확인하고, 실기 준비 후 `--execute MOVE`를 추가한다.

```bash
python3 scripts/replay_rby1_encoder.py --address 192.168.30.1:50051
```

## SSH에서 바퀴 이동

`scripts/rby1_drive.py`는 단발성, 저속(선속도 최대 0.15 m/s), 최대 1초의 모바일 베이스
명령만 보낸다. 전원·서보·Control Manager는 자동으로 켜지 않으며, 실행 전 주변을 비우고
EMO를 손에 닿는 곳에 둔다.

```bash
ssh nvidia@192.168.50.86 \
  'cd /home/nvidia/robot-project &&
   python3 rby1_drive.py --forward 0.10 --duration 0.5 --confirm-drive MOVE'
```

`--forward`는 전진(+)/후진(-), `--turn`은 회전(rad/s)이다. Model A는 `--sideways`를
지원하지 않고, Model M에서만 사용할 수 있다.

방향키 제어는 다음처럼 실행한다. 키를 누를 때마다 0.2초 저속 펄스를 보내며, `space`는
현재 제어 취소 요청, `q`는 종료다.

```bash
ssh -t nvidia@192.168.50.86 \
  'cd /home/nvidia/robot-project && python3 rby1_drive.py \
   --duration 0.2 --confirm-drive MOVE --interactive'
```
