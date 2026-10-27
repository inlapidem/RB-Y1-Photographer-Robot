# 실행 순서

터미널 5개. 순서를 지켜야 한다 — **로봇 전원이 스택보다 먼저**, **스택이 geekseek보다 먼저**다.

모든 터미널에서 먼저:

```bash
cd ~/Desktop/hackton/1st-robot-hackathon_geekseek
source /opt/ros/humble/setup.bash
```

## 빠른 실행 (붙여넣기용)

로봇 전원 → 초록등 → **기능버튼 1을 1초** → 10초 대기. 그 다음:

```bash
# 터미널 1
cat /sys/class/net/eno1/operstate
ros2 run agx_phorce_bridge phorce_monitor \
  --ros-args -p nic:=eno1 -p mode:=command -p axes:=2 -p mbx_enabled:=true

# 터미널 2
ros2 run agx_motion_slot motion_action_server --ros-args -p backend:=ecat

# 터미널 3
phorce doctor      # READY
phorce status      # physical idle: True
phorce list        # 슬롯 1~5
phorce play 4      # (선택) 팔이 28초 움직인다

# 터미널 4 — 한 번만
sudo tailscale funnel --bg 8080

# 터미널 5
python3 -m geekseek --config config/jetson-phorce.yaml
```

터미널 1~3은 기존 로봇 단독 점검 루틴 그대로이고, 4·5가 키오스크용으로 얹힌 것이다.
아래는 각 단계에서 확인할 것.

---

## 0. 슬롯 (웨이포인트를 새로 찍었을 때만)

```bash
python3 scripts/build_motion_slots.py                              # calibration/slots/ 에 생성
python3 scripts/build_motion_slots.py --out /media/phorce/<SD>/Motions   # SD에 반영
```

SD에 쓴 뒤 **언마운트 → PCM 전원 재투입**. 안 하면 PCM이 옛 파일을 계속 쓴다.

---

## 1. 로봇 켜기

1. 로봇 전원 인가
2. 상태등이 **초록**(파킹·서보 해제)이 될 때까지 대기
3. **기능버튼 1을 약 1초** 누름 → 3초 경고음 → 서보 켜지고 부팅 자세로 이동
4. 약 10초 대기

> 버튼 1을 안 누르면 `physical_idle=False`로 남아 재생이 코드 12(`NOT_READY_FOR_MOTION`)로 거절된다.
> 누르면 로봇이 실제로 움직인다 — 주변 확인.

---

## 2. 터미널 1 — EtherCAT 브리지

```bash
cat /sys/class/net/eno1/operstate          # up 이어야 함

ros2 run agx_phorce_bridge phorce_monitor \
  --ros-args -p nic:=eno1 -p mode:=command -p axes:=2 -p mbx_enabled:=true
```

확인할 로그:

```
적재 슬롯 마스크 0x000000000000003E     ← 슬롯 1~5. 4·5가 없으면 0x0E
phorce_monitor 시작 — mode=command
```

> `axes`는 **2**다. `command` 모드는 PCM이 2만 허용하고 다른 값은 기동이 거부된다.
> (`safe_op` 측정 모드에서만 `axes:=5`)

---

## 3. 터미널 2 — 모션 슬롯 서버

```bash
ros2 run agx_motion_slot motion_action_server --ros-args -p backend:=ecat
```

---

## 4. 터미널 3 — 점검

```bash
phorce doctor      # 판정: READY
phorce status      # physical idle: True,  state: IDLE
phorce list        # 슬롯 1~5
```

> `doctor`가 `Action 서버 없음`이라고 하면 몇 초 뒤 한 번 더 실행한다. CLI는 호출마다
> 새 프로세스라 DDS 탐색이 늦으면 첫 판정을 놓친다 — `ros2 action list`에 나오면 정상이다.
>
> `physical idle: False`면 **1번(버튼 1)을 안 한 것**이다.

동작 확인 (선택, **팔이 28초 움직인다**):

```bash
phorce play 4      # 전신
phorce play 5      # 상반신
```

---

## 5. 터미널 4 — 갤러리 공개 (한 번만)

```bash
sudo tailscale funnel --bg 8080
tailscale funnel status
```

나오는 `https://<...>.ts.net` 주소를 `config/jetson-phorce.yaml`의
`gallery_base_url`과 맞춘다. 이미 맞아 있으면 건너뛴다.

---

## 6. 터미널 5 — 키오스크

```bash
python3 -m geekseek --config config/jetson-phorce.yaml
```

확인할 로그:

```
[geekseek] gallery: https://<...>.ts.net (local :8080)     ← 있어야 함
[geekseek] 로봇 사전 검증 실패 — ...                         ← 없어야 함
```

> 사전 검증은 기동 시 1회만 돈다. 스택을 나중에 올렸다면 **geekseek을 재시작**한다.

---

## 7. 기기 연결

| 기기 | 주소 |
|---|---|
| iPad 1 | `https://<Jetson IP>:8443/face` |
| iPad 2 | `https://<Jetson IP>:8443/guide` |
| 촬영용 폰 | `https://<Jetson IP>:8443/phone` |
| 디버그 | `https://<Jetson IP>:8443/debug` |

- 자체서명 인증서라 첫 접속에서 "이 웹사이트 방문"을 1회 수락해야 한다
- 촬영용 폰은 `/phone`을 **열어둔 채로** 둬야 한다 (카메라 스트림 유지)
- iPad는 설정 → 가이드 접근 모드로 풀스크린 고정
- 손님 폰은 아무것도 안 해도 된다 — QR만 찍으면 된다

---

## 8. 한 사이클

```
사람 접근 → 인사 → 구도 선택(전신/상반신) → 위치 안내
  → 손 들어 준비 → 3·2·1 → 팔 28초 이동하며 40장 촬영
  → 미리보기 → QR → 손님 폰에서 전부 저장
```

---

## 9. 종료

```bash
# 터미널 5, 2, 1 순서로 Ctrl-C
sudo tailscale funnel --https=443 off     # 공개 노출 끄기
```

로봇은 기능버튼 2를 약 1초 눌러 정리 자세로 보낸 뒤 전원을 내린다.

---

## 비상

**정지 수단은 E-Stop 물리 버튼 하나뿐이다.** 발사한 모션은 코드로 멈출 수 없고
`cancel()`은 E-Stop이 아니다. 재생 중에는 항상 손이 닿는 곳에 둔다.
