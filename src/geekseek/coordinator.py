from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Coroutine

from .capture import CaptureDevice
from .framing_guide import (
    FULL_BODY,
    UPPER_BODY,
    FramingGuidance,
    SilhouetteTemplate,
    annotate_framing_frame,
    evaluate_framing,
    visible_points,
)
from .gallery import Gallery
from .perception import PersonSensor, PersonSignal, encode_jpeg, is_approaching, is_positioned, is_ready_signal
from .robot import Robot, burst_poses_for_template, pose_for_template
from .vlm import Greeter
from .workflow import Event, EventType, InvalidTransition, State, WorkflowContext, apply_event


FRAMING_STABLE_REQUIRED = 2

FrameSource = Callable[[], object | None]


class Coordinator:
    """Single writer for workflow state and owner of background effects."""

    def __init__(
        self,
        robot: Robot,
        capture: CaptureDevice,
        person_sensor: PersonSensor | None = None,
        frame_source: FrameSource | None = None,
        greeter: Greeter | None = None,
        sense_interval: float = 0.2,
        live_frame_interval: float = 1 / 15,
        greeting_seconds: float = 3.0,
        preview_seconds: float = 3.0,
        asking_seconds: float = 30.0,
        gesture_hold_seconds: float = 1.0,
        farewell_seconds: float = 4.0,
        countdown_seconds: float = 0.7,
        ready_timeout_seconds: float = 12.0,
        photo_target_count: int = 40,
        slide_seconds: float = 0.0,
        gallery: Gallery | None = None,
        framing_templates: dict[str, SilhouetteTemplate] | None = None,
    ) -> None:
        self.context = WorkflowContext()
        self.robot = robot
        self.capture = capture
        self.person_sensor = person_sensor
        self.frame_source = frame_source
        self.greeter = greeter
        self.sense_interval = sense_interval
        self.live_frame_interval = live_frame_interval
        self.greeting_seconds = greeting_seconds
        self.preview_seconds = preview_seconds
        self.asking_seconds = asking_seconds
        self.gesture_hold_seconds = gesture_hold_seconds
        self.farewell_seconds = farewell_seconds
        self.countdown_seconds = countdown_seconds
        self.ready_timeout_seconds = ready_timeout_seconds
        self.photo_target_count = photo_target_count
        self.slide_seconds = slide_seconds
        self.gallery = gallery
        self.framing_templates = framing_templates or {}
        self.context.photo_target = photo_target_count
        self.debug_frame: bytes | None = None
        self.rgb_frame: bytes | None = None
        self.live_frame: bytes | None = None
        self._last_signal = PersonSignal(detected=False)
        self._last_approaching = False
        self._last_positioned = False
        self._last_framing_guidance = FramingGuidance(False, "사람을 기다리는 중")
        self._last_framing_points = {}
        self._framing_stable_frames = 0
        self._teaching_guidance = {
            mode: FramingGuidance(False, "모델을 카메라 앞에 세워주세요")
            for mode in (FULL_BODY, UPPER_BODY)
        }
        self._teaching_points = {mode: {} for mode in (FULL_BODY, UPPER_BODY)}
        self._teaching_stable_frames = {mode: 0 for mode in (FULL_BODY, UPPER_BODY)}
        self._teaching_people_count = 0
        self._framing_debug_until = 0.0
        self.events: asyncio.Queue[Event | None] = asyncio.Queue()
        self._runner: asyncio.Task[None] | None = None
        self._sense_task: asyncio.Task[None] | None = None
        self._frame_task: asyncio.Task[None] | None = None
        self._effects: set[asyncio.Task[None]] = set()
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()
        self._ready_event = asyncio.Event()
        self._ready_neutral_seen = False
        self._choice_gesture: str | None = None
        self._choice_gesture_started_at: float | None = None
        self._choice_gesture_locked = False

    async def start(self) -> None:
        if self._runner is not None:
            return
        self._runner = asyncio.create_task(self._run())
        if hasattr(self.robot, "preflight"):
            # doctor/list/status 검증 후, 실제 RBY1 어댑터는 촬영을 받기 전에
            # 기록된 base pose로 한 번 복귀한다. Fake/Phorce/RViz에는
            # move_home이 없으므로 기존 시작 동작은 그대로 유지된다.
            # 실패해도 서버는 띄운다 — 로봇 스택을 아직
            # 안 올렸을 뿐일 수 있고, 그때 웹서버까지 못 뜨면 원인을 볼 화면조차
            # 없다. 대신 운영자가 반드시 보도록 콘솔로도 알린다: 키오스크 화면은
            # error 상태에서만 오버레이를 띄우는데, 여기서는 아직 booting이라
            # context.error만으로는 아무 데도 안 보인다.
            try:
                await self.robot.preflight()
                if hasattr(self.robot, "move_home"):
                    await self.robot.move_home()
            except Exception as exc:
                self.context.error = f"로봇 준비 실패: {exc}"
                print(f"[geekseek] 로봇 사전 검증 실패 — 촬영은 실패합니다: {exc}", file=sys.stderr)
        if self.person_sensor is not None:
            self._sense_task = asyncio.create_task(self._sense_loop())
            if self.frame_source is not None:
                self._frame_task = asyncio.create_task(self._frame_loop())
        await self.emit(EventType.SYSTEM_READY)
        await self.wait_for_state(State.WAITING)

    async def stop(self) -> None:
        if self._runner is None:
            return
        if self._sense_task is not None:
            self._sense_task.cancel()
            await asyncio.gather(self._sense_task, return_exceptions=True)
            self._sense_task = None
        if self._frame_task is not None:
            self._frame_task.cancel()
            await asyncio.gather(self._frame_task, return_exceptions=True)
            self._frame_task = None
        if hasattr(self.robot, "close"):
            self.robot.close()
        for task in self._effects:
            task.cancel()
        if self._effects:
            await asyncio.gather(*self._effects, return_exceptions=True)
        self._effects.clear()
        await self.events.put(None)
        await self._runner
        self._runner = None

    async def emit(self, event_type: EventType, **data: object) -> None:
        await self.events.put(Event(event_type, data))

    async def wait_for_state(self, state: State, timeout: float = 2.0) -> None:
        await asyncio.wait_for(self._wait_for_state(state), timeout)

    async def _wait_for_state(self, state: State) -> None:
        while self.context.state is not state:
            await asyncio.sleep(0.005)

    async def wait_for_revision(self, revision: int, timeout: float = 2.0) -> None:
        await asyncio.wait_for(self._wait_for_revision(revision), timeout)

    async def _wait_for_revision(self, revision: int) -> None:
        while self.context.revision < revision:
            await asyncio.sleep(0.005)

    async def updates(self) -> AsyncIterator[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        try:
            yield self.context.as_dict()
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def _run(self) -> None:
        while (event := await self.events.get()) is not None:
            try:
                apply_event(self.context, event)
            except InvalidTransition:
                continue
            self._publish()
            if self.context.state is State.GREETING:
                self._spawn(self._timer(EventType.GREETING_DONE, self.greeting_seconds))
                if self.greeter is not None:
                    self._spawn(self._generate_greeting())
            elif self.context.state is State.CAPTURING:
                self._spawn(self._capture_burst())
            elif self.context.state is State.PREVIEWING:
                self._spawn(self._timer(EventType.PREVIEW_DONE, self._preview_seconds_for_photos()))
            elif self.context.state is State.ASKING:
                self._spawn(self._timer(EventType.PHOTO_LIKED, self.asking_seconds))
            elif self.context.state is State.FAREWELL:
                self._spawn(self._timer(EventType.FAREWELL_DONE, self.farewell_seconds))

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._effects.add(task)
        task.add_done_callback(self._effects.discard)

    def _publish(self) -> None:
        snapshot = self.context.as_dict()
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot)

    def _preview_seconds_for_photos(self) -> float:
        """Hold the slideshow long enough to actually show every photo once.

        The guide cycles a photo every `slide_seconds`; a fixed 3 s was fine
        for a three-shot burst but shows less than a quarter of a sweep, so a
        guest would be asked to pick a favourite having seen eight of forty.

        Off (0) unless a config asks for it, so every existing entry point
        keeps exactly the preview timing it has today — only the phorce sweep,
        which is the thing that produces dozens of photos, opts in.
        """
        return max(self.preview_seconds, len(self.context.photos) * self.slide_seconds)

    async def _timer(self, event_type: EventType, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.emit(event_type)

    async def _sense_loop(self) -> None:
        """Runs for the whole session; only acts when the current state cares
        about a person signal (WAITING → 접근 감지, GUIDING → 정위치 확인)."""
        while True:
            await asyncio.sleep(self.sense_interval)
            if self.person_sensor is None:
                continue
            if self.frame_source is not None:
                frame = self.frame_source()
                if frame is None:
                    continue  # webcam hasn't produced a first frame yet
            else:
                frame = None
            signal: PersonSignal = self.person_sensor.sense(frame)
            approaching = is_approaching(signal)
            positioned = is_positioned(signal)
            self._update_teaching_framing(signal)
            mode = self._selected_framing_mode()
            debug_override = asyncio.get_running_loop().time() < self._framing_debug_until
            if self.context.state is State.GUIDING and debug_override:
                positioned = False
            elif self.context.state is State.GUIDING and mode in self.framing_templates:
                points = {}
                landmarks_list = getattr(self.person_sensor, "latest_landmarks", ())
                if signal.detected and len(landmarks_list) == 1:
                    points = visible_points(landmarks_list[0], 0.2)
                guidance = evaluate_framing(self.framing_templates[mode], points)
                self._framing_stable_frames = (
                    self._framing_stable_frames + 1 if guidance.positioned else 0
                )
                positioned = self._framing_stable_frames >= FRAMING_STABLE_REQUIRED
                message = guidance.message
                if guidance.positioned and not positioned:
                    message = "잠시 그대로 서 주세요"
                self._last_framing_points = points
                self._last_framing_guidance = guidance
                self._patch_framing(guidance, message, positioned)
            else:
                self._framing_stable_frames = 0
            self._last_signal = signal
            self._last_approaching = approaching
            self._last_positioned = positioned
            if self.context.state is State.WAITING and approaching:
                await self.emit(EventType.PERSON_APPROACHED)
            elif self.context.state is State.DECIDING:
                await self._handle_choice_gesture(signal)
            elif self.context.state is State.GUIDING and positioned:
                await self.emit(EventType.POSITION_REACHED)
            elif (
                self.context.state is State.CAPTURING
                and self.context.awaiting_ready
            ):
                # The hand used for framing selection must be lowered once
                # before a new raise can arm physical motion.  Otherwise one
                # continuous hold can both select a mode and start the robot.
                if not signal.hand_raised:
                    self._ready_neutral_seen = True
                elif self._ready_neutral_seen and is_ready_signal(signal):
                    self._ready_event.set()

            if self.context.state is not State.DECIDING:
                self._choice_gesture = None
                self._choice_gesture_started_at = None
                self._choice_gesture_locked = False

    async def _handle_choice_gesture(self, signal: PersonSignal) -> None:
        """One guest holds one hand for a second to choose the framing mode."""
        choice: str | None = None
        if signal.detected and signal.people_count == 1:
            if signal.left_hand_raised and not signal.right_hand_raised:
                choice = "full_body"
            elif signal.right_hand_raised and not signal.left_hand_raised:
                choice = "upper_body"

        if choice is None:
            self._choice_gesture = None
            self._choice_gesture_started_at = None
            self._choice_gesture_locked = False
            if self.context.gesture_choice or self.context.gesture_progress:
                self._patch(gesture_choice="", gesture_progress=0.0)
            return

        now = asyncio.get_running_loop().time()
        if choice != self._choice_gesture:
            self._choice_gesture = choice
            self._choice_gesture_started_at = now
            self._choice_gesture_locked = False
            self._patch(gesture_choice=choice, gesture_progress=0.0)
            return

        if self._choice_gesture_locked or self._choice_gesture_started_at is None:
            return
        progress = min(1.0, (now - self._choice_gesture_started_at) / max(self.gesture_hold_seconds, 0.01))
        self._patch(gesture_choice=choice, gesture_progress=round(progress, 2))
        if progress >= 1.0:
            self._choice_gesture_locked = True
            await self.emit(EventType.CAPTURE_STARTED, template_id=choice)

    async def _frame_loop(self) -> None:
        """Refreshes the guest-facing live/debug camera preview at the
        camera's own capture rate, independent of sense_interval. Pose
        *detection* only needs a few samples a second to drive state
        transitions, but a guest watching themselves in the live mirror
        preview perceives anything slower than ~10fps as stutter/ghosting —
        redrawing the skeleton overlay onto each new frame is cheap (reuses
        the landmarks from the last sense() call) so this can run much
        faster than the sense loop."""
        while True:
            await asyncio.sleep(self.live_frame_interval)
            frame = self.frame_source()
            if frame is None:
                continue
            self.rgb_frame = encode_jpeg(frame)
            if hasattr(self.person_sensor, "annotate_jpeg"):
                self.debug_frame = self.person_sensor.annotate_jpeg(
                    frame, self._last_signal, self._last_approaching, self._last_positioned
                )
            if hasattr(self.person_sensor, "mirror_jpeg"):
                live_frame = frame
                mode = self._selected_framing_mode()
                if self.context.state is State.GUIDING and mode in self.framing_templates:
                    live_frame = annotate_framing_frame(
                        frame,
                        self.framing_templates[mode],
                        self._last_framing_points,
                        self._last_framing_guidance,
                    )
                self.live_frame = self.person_sensor.mirror_jpeg(live_frame)

    def _selected_framing_mode(self) -> str | None:
        return {
            "full_body": FULL_BODY,
            "upper_body": UPPER_BODY,
        }.get(self.context.template_id or "")

    @staticmethod
    def _mode_for_template(template_id: str) -> str | None:
        return {"full_body": FULL_BODY, "upper_body": UPPER_BODY}.get(template_id)

    def _update_teaching_framing(self, signal: PersonSignal) -> None:
        """Evaluate both recorder compositions without changing workflow state."""
        landmarks_list = getattr(self.person_sensor, "latest_landmarks", ())
        self._teaching_people_count = len(landmarks_list)
        for mode in (FULL_BODY, UPPER_BODY):
            template = self.framing_templates.get(mode)
            points = {}
            if signal.detected and len(landmarks_list) == 1:
                points = visible_points(landmarks_list[0], 0.2)
            if len(landmarks_list) > 1:
                guidance = FramingGuidance(False, "한 명만 촬영 위치에 서 주세요")
            elif template is None:
                guidance = self._fallback_teaching_guidance(signal)
            else:
                guidance = evaluate_framing(template, points)
            self._teaching_points[mode] = points
            self._teaching_guidance[mode] = guidance
            self._teaching_stable_frames[mode] = (
                self._teaching_stable_frames[mode] + 1 if guidance.positioned else 0
            )

    @staticmethod
    def _fallback_teaching_guidance(signal: PersonSignal) -> FramingGuidance:
        """Center/size guide used until a site-specific silhouette is recorded."""
        if not signal.detected:
            return FramingGuidance(False, "모델을 RGB 카메라 앞에 세워주세요")
        target_size = 0.18
        scale_ratio = (max(signal.size_ratio, 1e-6) / target_size) ** 0.5
        center_error_x = signal.center_x - 0.5
        if signal.size_ratio < 0.12:
            message, direction = "앞으로 이동하세요", "forward"
        elif signal.size_ratio > 0.28:
            message, direction = "뒤로 이동하세요", "back"
        elif center_error_x < -0.10:
            message, direction = "화면 오른쪽으로 이동하세요", "right"
        elif center_error_x > 0.10:
            message, direction = "화면 왼쪽으로 이동하세요", "left"
        elif not 0.28 <= signal.center_y <= 0.82:
            message, direction = "카메라 중앙 표시 안으로 이동하세요", "align"
        else:
            message, direction = "좋습니다 · 그대로 서 주세요", "hold"
        return FramingGuidance(
            detected=True,
            message=message,
            direction=direction,
            scale_ratio=scale_ratio,
            center_error_x=center_error_x,
            inside_count=1 if direction == "hold" else 0,
            required_count=1,
            positioned=direction == "hold",
        )

    def teaching_framing_snapshot(self, template_id: str) -> dict[str, object]:
        mode = self._mode_for_template(template_id)
        if mode is None:
            raise ValueError(f"지원하지 않는 촬영 모드입니다: {template_id}")
        guidance = self._teaching_guidance[mode]
        stable_frames = self._teaching_stable_frames[mode]
        landmarks = [
            {
                "index": index,
                "x": round(point.x, 6),
                "y": round(point.y, 6),
                "visibility": round(point.visibility, 6),
            }
            for index, point in sorted(self._teaching_points[mode].items())
        ]
        return {
            "template_id": template_id,
            "available": self.person_sensor is not None,
            "algorithm": "silhouette" if mode in self.framing_templates else "center_size",
            "detected": guidance.detected,
            "people_count": self._teaching_people_count,
            "message": guidance.message if stable_frames < FRAMING_STABLE_REQUIRED else "좋습니다 · 그대로 서 주세요",
            "direction": guidance.direction,
            "scale_ratio": round(guidance.scale_ratio, 3),
            "center_error_x": round(guidance.center_error_x, 3),
            "inside_count": guidance.inside_count,
            "required_count": guidance.required_count,
            "raw_positioned": guidance.positioned,
            "stable_frames": min(stable_frames, FRAMING_STABLE_REQUIRED),
            "stable_required": FRAMING_STABLE_REQUIRED,
            "positioned": stable_frames >= FRAMING_STABLE_REQUIRED,
            "landmarks": landmarks,
        }

    def teaching_framing_jpeg(self, template_id: str) -> bytes | None:
        mode = self._mode_for_template(template_id)
        if mode is None:
            raise ValueError(f"지원하지 않는 촬영 모드입니다: {template_id}")
        if self.frame_source is None or self.person_sensor is None:
            return None
        frame = self.frame_source()
        if frame is None:
            return None
        template = self.framing_templates.get(mode)
        if template is not None:
            frame = annotate_framing_frame(
                frame,
                template,
                self._teaching_points[mode],
                self._teaching_guidance[mode],
            )
        elif hasattr(self.person_sensor, "annotate_jpeg"):
            return self.person_sensor.annotate_jpeg(
                frame,
                self._last_signal,
                self._last_approaching,
                self._teaching_stable_frames[mode] >= FRAMING_STABLE_REQUIRED,
            )
        if hasattr(self.person_sensor, "mirror_jpeg"):
            return self.person_sensor.mirror_jpeg(frame)
        return encode_jpeg(frame)

    def _patch_framing(
        self,
        guidance: FramingGuidance,
        message: str,
        positioned: bool,
    ) -> None:
        values = {
            "framing_message": message,
            "framing_direction": guidance.direction,
            "framing_scale": round(guidance.scale_ratio, 3),
            "framing_inside": guidance.inside_count,
            "framing_required": guidance.required_count,
            "framing_positioned": positioned,
        }
        if any(getattr(self.context, key) != value for key, value in values.items()):
            self._patch(**values)

    async def _generate_greeting(self) -> None:
        """Fire-and-forget VLM caption for the greeting caption. Never blocks
        the timer-driven GREETING_DONE transition; applies the result only if
        the cycle hasn't already moved past deciding (stale response guard)."""
        if self.greeter is None or self.frame_source is None:
            return
        frame = self.frame_source()
        if frame is None:
            return
        jpeg = encode_jpeg(frame)
        if not jpeg:
            return
        line = await self.greeter.greet(jpeg)
        if line and self.context.state in (State.GREETING, State.DECIDING):
            self._patch(greeting_line=line)

    async def _wait_until_ready(self) -> None:
        """정위치 도달 직후, 카운트다운 전에 "손 들어 준비완료" 신호를 기다린다.
        person_sensor가 없으면(테스트/시뮬레이션) 곧바로 통과. _sense_loop가
        awaiting_ready=True인 동안 is_ready_signal을 감지하면 _ready_event를
        세팅한다 — 여기서 프레임을 직접 sense()하지 않아 중복 추론을 피한다."""
        if self.person_sensor is None:
            return
        self._ready_event.clear()
        self._ready_neutral_seen = False
        self._patch(awaiting_ready=True)
        try:
            await asyncio.wait_for(self._ready_event.wait(), self.ready_timeout_seconds)
        except asyncio.TimeoutError:
            pass  # 손을 못 들었어도 계속 기다리게 하지 않고 진행 (fail-safe)
        self._patch(awaiting_ready=False)

    def force_ready(self) -> bool:
        """운영자가 "준비완료(손들기)" 인식을 대신 눌러준다.

        관람객이 몰리면 손을 든 사람이 손님인지 구경꾼인지 구분이 안 되고,
        그동안 손님은 ready_timeout_seconds(12초)를 멀뚱히 서서 기다린다.
        지금 그 신호를 기다리는 중일 때만 먹는다."""
        if not self.context.awaiting_ready:
            return False
        self._ready_event.set()
        return True

    async def _capture_burst(self) -> None:
        # A WebAppCapture failure is otherwise discovered only at the first
        # dwell, after the physical arm has already moved.  Refuse the run
        # before ready/countdown whenever the phone WebSocket is absent.
        if hasattr(self.capture, "connected") and not self.capture.connected:
            await self.emit(EventType.CAPTURE_FAILED, reason="iPhone camera is not connected")
            return
        await self._wait_until_ready()

        for remaining in (3, 2, 1):
            self._patch(countdown=remaining)
            await asyncio.sleep(self.countdown_seconds)
        self._patch(countdown=None)

        photos: list[str] = []
        files: list[Path] = []

        async def shoot(_: object = None) -> None:
            result = await self.capture.capture()
            photos.append(result.photo_url)
            if result.path is not None:
                files.append(result.path)
            # Publish after every shot (not just at the end) so the guide
            # screen can flash each captured frame as it lands instead of
            # only jumping from 0 to N at burst completion.
            self._patch(photos=list(photos))

        sweeping = hasattr(self.robot, "sweep")
        poses = burst_poses_for_template(self.context.template_id or "")
        # The two paths take wildly different shot counts, so publish the one
        # that's actually about to run — the guide screen shows it as "n / N".
        self._patch(photo_target=self.photo_target_count if sweeping else len(poses))

        try:
            if sweeping:
                # Real arm: one pre-loaded slot sweeps every framing while the
                # shutter fires on the schedule baked into that slot.
                await self.robot.sweep(
                    pose_for_template(self.context.template_id or ""),
                    shoot,
                    self.photo_target_count,
                )
            else:
                for pose in poses:
                    await self.robot.move_to(pose)
                    await shoot()
        except Exception as exc:
            await self.emit(EventType.CAPTURE_FAILED, reason=str(exc))
        else:
            if self.gallery is not None and self.gallery.enabled and files:
                # 이 촬영 건만 열리는 링크. 사진 디렉토리를 통째로 노출하면
                # 앞 손님 사진까지 같이 보인다.
                self._patch(gallery_url=self.gallery.url_for(self.gallery.publish(files)))
            await self.emit(EventType.BURST_COMPLETE, photos=photos)

    def _patch(self, **fields: object) -> None:
        """Update context fields directly, outside apply_event, for data that
        isn't a state transition (countdown ticks, in-progress photos) —
        same idea as _generate_greeting's greeting_line update."""
        for key, value in fields.items():
            setattr(self.context, key, value)
        self.context.revision += 1
        self._publish()
