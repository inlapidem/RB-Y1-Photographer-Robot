# Geekseek RViz fake robot

`Assemble_CAM.step` 조립 트리에서 추출한 실제 키오스크·iPad·카메라 형상과 5축 로봇 링크를 사용하는
테스트 모델이다. 앱의 `RvizRobot`은
`/geekseek/fake_robot/target`에 `frame.full_body`, `frame.upper_body`,
`frame.product_closeup` 같은 의미 기반 포즈를 발행하고, `completed:<pose>` 피드백을 받은 뒤에만
상태 머신을 `GUIDING`으로 전환한다. 기본 RViz 시점은 고정 iPad 거치대와 움직이는 로봇측 iPad 마운트를
한 화면에서 볼 수 있는 측면 사선이다.

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths ros --symlink-install
source install/setup.bash
ros2 launch geekseek_fake_robot display.launch.py
```

이동 시간을 바꾸거나 GUI 없이 노드만 검증할 수도 있다.

```bash
ros2 launch geekseek_fake_robot display.launch.py move_seconds:=2.0
ros2 launch geekseek_fake_robot display.launch.py use_rviz:=false
ros2 launch geekseek_fake_robot display.launch.py use_fake_robot:=false
```

마지막 명령은 외부 `/joint_states`를 넣어 관절별 rigid 관계를 진단할 때 사용한다.

## STEP 메시 다시 생성

런타임에는 CAD 라이브러리가 필요 없다. STEP가 바뀌었을 때만 CAD 선택 의존성을 설치하고 메시를 갱신한다.

```bash
python3 -m pip install -e '.[cad]'
python3 tools/extract_step_links.py \
  assets/cad/Assemble_CAM.step \
  ros/geekseek_fake_robot/meshes
```

추출기는 STEP 최상위 구성과 36개 관절 부품 이름을 검증한 뒤 생성하며, 정확한 부품 귀속은
`meshes/manifest.json`에 남는다. STEP AP214의 형상으로부터 축과 rigid 그룹을 추론한 것이므로 실제 phorce
관절 방향과 대조한 뒤 포즈 각도를 최종 확정한다.

다른 터미널에서 상태 머신 전체 흐름을 실행한다.

```bash
PYTHONPATH="src:${PYTHONPATH}" python3 -m geekseek --config config/rviz.yaml --demo
```

ROS Humble는 시스템 Python 3.10을 사용한다. Conda Python을 함께 쓸 때도 위처럼 기존 `PYTHONPATH`를
보존해야 ROS CLI 패키지를 찾을 수 있다.
