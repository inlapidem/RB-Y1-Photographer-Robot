# RB-Y1 Photographer Robot

A kiosk robot that photographs guests, run live at the opening ceremony of an SNU robotics institute (August 2026). A Rainbow Robotics RB-Y1 holds a phone in its right gripper and sweeps it through a set of taught camera poses, shooting 30 photos per guest.

Demo video: https://youtu.be/OhhxrzGfgDo

## Based on GeekSeek

This project is adapted from **GeekSeek**, built by team GeekSeek (SNU; 김민재, 박상원, 강채은) for the 1st Robot Hackathon hosted by Roboticus (Aug 5–8, 2026, KAIST).

- Original repository: https://github.com/robotic-us/1st-robot-hackathon_geekseek
- Original robot: Phorce arm, driven by motion slots pre-loaded on its controller

The original GeekSeek system already included the eight-stage kiosk workflow, MediaPipe pose tracking on a Jetson Orin, silhouette-based framing guidance, the iPad kiosk screens, the phone-camera shutter over WebSocket, the QR photo gallery and the Claude-generated greeting.

Intellectual property of the original project belongs to the GeekSeek team (all members), as stated in the original repository.

## What changed for the RB-Y1

- **RB-Y1 adapter** (`Rby1Robot` in `src/geekseek/robot.py`): drives the right arm through `rby1_sdk`. It never powers on the robot or enables servos itself. It only checks that the Control Manager is enabled and returns to the taught `start-home` pose on startup.
- **Taught keyposes instead of copied joint values**: 11 final camera poses are taught by hand in gravity-compensation mode with the phone held in the gripper (`scripts/record_rby1_right_arm.py`).
- **Exact tour planning** (`src/geekseek/rby1_keyposes.py`): a Held–Karp dynamic program finds the shortest `home → 11 poses → home` order. `blocked_edges` forbids unsafe pose pairs. The 30 shots are split between stops at each pose and moving shots spread over the travel segments in proportion to their length.
- **Limit-aware retiming**: each move is timed from the right arm's joint velocity and acceleration limits (70% and 50%), and shots fire when the commanded segment actually completes rather than on a wall-clock schedule.
- **Gesture control**: holding up the left hand for one second picks a full-body shot, and the right hand picks an upper-body shot. The hand must be lowered once before the "ready" raise can start the robot.
- **On-site teaching tools**: live framing evaluation for both shot modes, camera selection by device name, and separate device and processing frame rates.

At the event it completed 27 sessions (810 photos) with no failed capture.

## Running

```bash
python3 -m pip install -e '.[rby1,dev]'
cp config/jetson-rby1.example.yaml config/jetson-rby1.yaml
python3 -m geekseek --config config/jetson-rby1.yaml
```

Without a robot, `python3 -m geekseek --config config/dev.yaml` runs the kiosk with a fake robot. See [`docs/RBY1_MIGRATION.md`](docs/RBY1_MIGRATION.md) and [`docs/RBY1_RUNBOOK.md`](docs/RBY1_RUNBOOK.md) for the RB-Y1 setup and teaching procedure, and [`docs/FINAL_METHOD.md`](docs/FINAL_METHOD.md) for the original Phorce setup.

HTTPS certificates (`certs/`) and the public tunnel hostname (`<your-tailnet>.ts.net` in `config/`) are not included. Guest photos, logs and CAD files are also left out of this repository.
