# GeekSeek RBY1 시연 실행 가이드

이 문서는 **RB-Y1 전원 준비부터 GeekSeek 서버 실행까지** 필요한 명령과 확인 사항만
정리한 현장용 runbook이다. 기준 Jetson은 `nvidia@192.168.50.86`, 프로젝트는
`/home/nvidia/robot-project/1st-robot-hackathon_geekseek`에 있다.

## 1. 로봇과 촬영 장비 준비

1. 로봇 주변을 비우고 EMO가 손에 닿는지 확인한다.
2. 휴대폰을 오른쪽 그리퍼에 같은 위치와 방향으로 단단히 고정한다.
3. RGB 카메라와 휴대폰 케이블 간섭 여부를 확인한다.
4. RB-Y1 Control Panel에 연결한 뒤 다음 순서로 준비한다.
   - 전원 ON
   - Servo ON
   - fault가 있으면 원인을 확인한 뒤 reset
   - Control Manager `ENABLE`

GeekSeek 코드는 안전을 위해 전원, Servo, Control Manager를 자동으로 켜지 않는다.

## 2. Jetson 접속과 사전 확인

노트북에서 Jetson에 접속한다.

```bash
ssh nvidia@192.168.50.86
```

Jetson에서 프로젝트 환경을 준비한다.

```bash
cd /home/nvidia/robot-project/1st-robot-hackathon_geekseek
source /home/nvidia/miniforge3/etc/profile.d/conda.sh
conda activate geekseek
```

로봇 연결, Control Manager와 오른손 F/T 센서를 **읽기 전용**으로 확인한다.

```bash
PYTHONPATH=src python scripts/check_rby1_eef.py \
  --address 192.168.30.1:50051
```

카메라와 포트 점유 상태를 확인한다. RGB 카메라는 `camera_name: Nuroum V11`로 찾으므로
`/dev/videoN` 번호가 바뀌어도 된다. 8443은 메인 서버, 8080은 QR 갤러리다.

```bash
ls -l /dev/v4l/by-id/*Nuroum*video-index0
ss -ltnp | grep -E ':8443|:8080'
pgrep -af 'python -m geekseek'
```

이미 `jetson-rby1.yaml` 서버가 떠 있으면 새 서버를 중복 실행하지 않는다. 중복 실행하면
기존 프로세스가 `/dev/video1`을 점유해 새 서버가 시작되지 않는다.

## 3. GeekSeek 서버 실행

포그라운드 실행은 다음 한 줄이면 된다. 로그를 바로 확인할 수 있어 시연 전 점검에
권장한다.

```bash
cd /home/nvidia/robot-project/1st-robot-hackathon_geekseek && \
source /home/nvidia/miniforge3/etc/profile.d/conda.sh && \
conda activate geekseek && \
python -m geekseek --config config/jetson-rby1.yaml
```

터미널을 닫아도 계속 실행하려면 다음처럼 백그라운드로 시작한다.

```bash
cd /home/nvidia/robot-project/1st-robot-hackathon_geekseek
setsid -f bash -lc 'source /home/nvidia/miniforge3/etc/profile.d/conda.sh && conda activate geekseek && cd /home/nvidia/robot-project/1st-robot-hackathon_geekseek && exec python -m geekseek --config config/jetson-rby1.yaml >> /tmp/geekseek-rby1.log 2>&1'
```

정상 시작 여부를 확인한다.

```bash
curl -sk https://127.0.0.1:8443/api/state
ss -ltnp | grep -E ':8443|:8080'
tail -f /tmp/geekseek-rby1.log
```

정상 기준은 8443과 127.0.0.1:8080이 모두 LISTEN이고, API 상태가 `waiting`이며,
로그에 `delegate=gpu`가 출력되는 것이다.

## 4. 화면 접속

같은 Wi-Fi에서 다음 주소를 연다.

| 기기 | 주소 | 용도 |
|---|---|---|
| StandbyMe 세로 화면 | `https://192.168.50.86:8443/standby` | 안내·촬영·QR UI |
| 촬영용 iPhone | `https://192.168.50.86:8443/phone` | 실제 사진 전송 |
| 운영 노트북 | `https://192.168.50.86:8443/debug` | 상태 확인과 비상 스킵 |

자체 서명 인증서 경고가 나오면 각 기기에서 한 번 접속을 허용한다. iPhone은 카메라
권한을 허용하고 `/phone`을 계속 열어 둔다.

iPhone 연결을 확인한다.

```bash
curl -sk https://127.0.0.1:8443/api/debug/phone-camera
```

`"connected": true`가 아니면 안전장치가 로봇 촬영 시작을 거부한다.

## 5. 시연 흐름

1. 한 명이 카메라 앞으로 오면 `waiting → greeting → deciding`으로 진행한다.
2. 사용자가 한 손을 1초 동안 든다.
   - 사용자 **왼손**: 전신 촬영
   - 사용자 **오른손**: 상반신 촬영
   - 두 손 또는 두 명 이상: 선택하지 않음
3. 화면의 어깨·골반 가이드에 몸을 맞춘다.
   - `framing_samples.csv`의 상반신 72개, 전신 75개 샘플을 각각 사용한다.
   - 좌우 어깨와 좌우 골반 허용 범위는 기록된 CSV 범위의 2배다.
   - 정위치가 연속 2프레임 인식되면 4단계에서 5단계로 넘어간다.
4. 모드 선택에 쓴 손을 한 번 완전히 내린 뒤 다시 손을 들면 촬영을 시작한다.
5. `3 → 2 → 1` 카운트다운(약 2.1초) 후 실제 RB-Y1 오른팔 궤적과 iPhone 촬영이 실행된다.
   - 상반신: 핵심 pose 10장 + 이동 중 20장
   - 전신: 핵심 pose 11장 + 이동 중 19장
   - 핵심 pose 정지 명령: 각 0.1초
   - 총 30장 촬영 후 base pose로 복귀
6. 미리보기와 QR을 표시한 뒤 자동으로 인사하고 `waiting`으로 복귀한다.

QR은 다음 Tailscale Funnel 공개 주소의 일회성 갤러리 링크를 담는다. 방문객은
Tailscale 앱이 필요 없다.

```text
https://tegra-ubuntu.<your-tailnet>.ts.net
```

## 6. Tailscale과 QR 확인

```bash
systemctl is-active tailscaled
tailscale status
tailscale funnel status
curl -I https://tegra-ubuntu.<your-tailnet>.ts.net
```

`tailscaled`가 `active`이고 Funnel이 `127.0.0.1:8080`으로 연결되어야 한다. 루트 URL의
404는 정상이다. 실제 갤러리는 촬영 후 생성되는 `/g/<token>` 주소에만 존재한다.

## 7. 안전한 종료와 재시작

로봇이 움직이는 `capturing` 상태에서는 서버를 종료하거나 재시작하지 않는다. 먼저 상태가
`waiting`인지 확인한다.

```bash
curl -sk https://127.0.0.1:8443/api/state
pgrep -af 'python -m geekseek --config config/jetson-rby1.yaml'
```

포그라운드 서버는 `Ctrl+C`로 종료한다. 백그라운드 서버는 위 명령으로 확인한 **정확한
PID 하나만** 종료한다.

```bash
kill <PID>
```

브라우저 SSE 연결 때문에 `Shutting down`에서 오래 기다릴 때만, 로봇이 `waiting`이고
완전히 정지한 것을 다시 확인한 뒤 같은 PID에 강제 종료를 사용한다.

```bash
kill -KILL <PID>
```

종료 후 8443과 8080 포트가 사라졌는지 확인하고 3절의 명령으로 다시 실행한다.

## 8. 자주 보는 문제

### `could not open camera index 1`

기존 GeekSeek 프로세스가 카메라를 점유한 경우가 대부분이다.

```bash
pgrep -af 'python -m geekseek'
fuser /dev/v4l/by-id/*Nuroum*video-index0
```

### `Control Manager가 ENABLE 상태가 아닙니다`

Control Panel에서 주변 안전, 전원, Servo, fault를 확인하고 Control Manager를 ENABLE로
바꾼다. 코드로 우회하지 않는다.

### `iPhone camera is not connected`

iPhone에서 `/phone`을 새로고침하고 카메라 권한과 화면 켜짐 상태를 확인한다.

### QR이 나타나지 않음

```bash
ss -ltnp | grep ':8080'
tailscale funnel status
grep gallery_base_url config/jetson-rby1.yaml
```

### Jetson 패키지 경고

현재 장비에는 이전부터 미완료 상태인 일부 `nvidia-l4t-*` 커널 패키지가 있다. 시연 직전에
`apt upgrade`, `apt --fix-broken install`, 커널 변경이나 재부팅을 임의로 실행하지 않는다.
Tailscale과 GeekSeek 동작에는 현재 영향이 없다.

## 핵심 명령만 다시 보기

```bash
# 접속
ssh nvidia@192.168.50.86

# 환경
cd /home/nvidia/robot-project/1st-robot-hackathon_geekseek
source /home/nvidia/miniforge3/etc/profile.d/conda.sh
conda activate geekseek

# 서버 실행
python -m geekseek --config config/jetson-rby1.yaml

# 상태
curl -sk https://127.0.0.1:8443/api/state
curl -sk https://127.0.0.1:8443/api/debug/phone-camera
tail -f /tmp/geekseek-rby1.log

# QR 터널
tailscale funnel status

# 프로세스
pgrep -af 'python -m geekseek --config config/jetson-rby1.yaml'
```
