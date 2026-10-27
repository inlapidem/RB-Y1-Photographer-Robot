from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .capture import WebAppCapture
from .coordinator import Coordinator
from .gallery import qr_svg
from .workflow import EventType, State


WEB_ROOT = Path(__file__).resolve().parents[2] / "web"

_VALID_TEMPLATES = {"full_body", "upper_body", "product_closeup"}


def create_app(coordinator: Coordinator) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await coordinator.start()
        yield
        await coordinator.stop()

    app = FastAPI(title="Geekseek Kiosk", lifespan=lifespan)
    app.state.coordinator = coordinator

    @app.middleware("http")
    async def no_cache_static(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            # Dev iterates on mock.js/mock.css constantly — a stale cached copy
            # in an already-open kiosk tab looks exactly like a regression.
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")

    if isinstance(coordinator.capture, WebAppCapture):
        capture = coordinator.capture
        capture.save_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/photos", StaticFiles(directory=capture.save_dir), name="photos")

        @app.get("/phone", include_in_schema=False)
        async def phone_page() -> FileResponse:
            return FileResponse(WEB_ROOT / "phone_capture.html")

        @app.websocket("/phone-ws")
        async def phone_ws(websocket: WebSocket) -> None:
            await websocket.accept()
            capture.bind(websocket)
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    if message.get("bytes") is not None:
                        capture.on_frame(message["bytes"])
                    elif message.get("text") is not None:
                        try:
                            metadata = json.loads(message["text"])
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if metadata.get("type") == "camera_metadata":
                            capture.update_camera_metadata(metadata)
            except WebSocketDisconnect:
                pass
            finally:
                capture.unbind(websocket)

        @app.get("/api/debug/phone-camera", include_in_schema=False)
        async def debug_phone_camera() -> dict[str, object]:
            return {
                "connected": capture.connected,
                "metadata": capture.camera_metadata,
            }

        @app.post("/api/debug/phone-snapshot", include_in_schema=False)
        async def debug_phone_snapshot() -> dict[str, object]:
            try:
                result = await capture.capture()
            except (RuntimeError, asyncio.TimeoutError) as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if result.path is None:
                raise HTTPException(status_code=500, detail="captured frame has no file")
            from PIL import Image

            with Image.open(result.path) as image:
                width, height = image.size
                orientation = image.getexif().get(274)
            return {
                "photo_url": result.photo_url,
                "width": width,
                "height": height,
                "exif_orientation": orientation,
                "metadata": capture.camera_metadata,
            }

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_ROOT / "debug.html")

    @app.get("/face", include_in_schema=False)
    async def face() -> FileResponse:
        return FileResponse(WEB_ROOT / "face-mock.html")

    @app.get("/guide", include_in_schema=False)
    async def guide() -> FileResponse:
        return FileResponse(WEB_ROOT / "guide-mock.html")

    @app.get("/standby", include_in_schema=False)
    async def standby() -> FileResponse:
        """Single portrait display used by the RB-Y1 installation."""
        return FileResponse(WEB_ROOT / "standby.html")

    @app.get("/debug", include_in_schema=False)
    async def debug() -> FileResponse:
        return FileResponse(WEB_ROOT / "debug.html")

    def _mjpeg_stream(get_frame):
        async def stream():
            boundary = b"--frame\r\n"
            while True:
                frame = get_frame()
                if frame:
                    yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                await asyncio.sleep(0.05)

        return StreamingResponse(
            stream(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-cache"},
        )

    if coordinator.person_sensor is not None and hasattr(coordinator.person_sensor, "annotate_jpeg"):

        @app.get("/debug/webcam", include_in_schema=False)
        async def debug_webcam() -> StreamingResponse:
            return _mjpeg_stream(lambda: coordinator.debug_frame)

        @app.get("/api/debug/rgb-frame", include_in_schema=False)
        async def rgb_frame() -> Response:
            if not coordinator.rgb_frame:
                raise HTTPException(status_code=503, detail="RGB 프레임을 기다리는 중입니다")
            return Response(
                coordinator.rgb_frame,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store, max-age=0"},
            )

        @app.get("/api/debug/teaching-framing/{template_id}", include_in_schema=False)
        async def teaching_framing(template_id: str) -> dict[str, object]:
            try:
                return coordinator.teaching_framing_snapshot(template_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        @app.get("/api/debug/teaching-framing-frame/{template_id}", include_in_schema=False)
        async def teaching_framing_frame(template_id: str) -> Response:
            try:
                image = coordinator.teaching_framing_jpeg(template_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if not image:
                raise HTTPException(status_code=503, detail="skeleton 프레임을 기다리는 중입니다")
            return Response(
                image,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store, max-age=0"},
            )

    if coordinator.person_sensor is not None and hasattr(coordinator.person_sensor, "mirror_jpeg"):

        @app.get("/live/camera", include_in_schema=False)
        async def live_camera() -> StreamingResponse:
            return _mjpeg_stream(lambda: coordinator.live_frame)

    @app.get("/api/state")
    async def get_state() -> dict[str, object]:
        return coordinator.context.as_dict()

    async def emit(event_type: EventType, allowed: set[State], **data: object) -> dict[str, object]:
        if coordinator.context.state not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"{event_type.value} is invalid in {coordinator.context.state.value}",
            )
        revision = coordinator.context.revision
        await coordinator.emit(event_type, **data)
        await coordinator.wait_for_revision(revision + 1)
        return coordinator.context.as_dict()

    @app.post("/api/capture-started")
    async def capture_started(payload: dict[str, str]) -> dict[str, object]:
        template_id = payload.get("template_id", "")
        if template_id not in _VALID_TEMPLATES:
            raise HTTPException(status_code=422, detail="unknown template_id")
        return await emit(
            EventType.CAPTURE_STARTED,
            {State.DECIDING},
            template_id=template_id,
        )

    @app.post("/api/decline")
    async def decline() -> dict[str, object]:
        return await emit(EventType.DECLINED, {State.DECIDING})

    @app.post("/api/replay")
    async def replay() -> dict[str, object]:
        return await emit(EventType.REPLAY_REQUESTED, {State.ASKING})

    @app.post("/api/liked")
    async def liked() -> dict[str, object]:
        return await emit(EventType.PHOTO_LIKED, {State.ASKING})

    @app.post("/api/reset")
    async def reset() -> dict[str, object]:
        return await emit(EventType.RESET_REQUESTED, {State.ERROR})

    @app.post("/api/debug/person-approached", include_in_schema=False)
    async def debug_person_approached() -> dict[str, object]:
        # Forces waiting->greeting. 부스에 사람이 몰리면 지나가는 구경꾼도
        # size_ratio 기준을 넘겨서 접근 판정이 손님을 특정하지 못한다.
        return await emit(EventType.PERSON_APPROACHED, {State.WAITING})

    @app.post("/api/debug/position-reached", include_in_schema=False)
    async def debug_position_reached() -> dict[str, object]:
        # Forces guiding->capturing without waiting for the webcam to see
        # someone centered — handy for testing the countdown/burst UI without
        # a person in front of the camera. 현장에서는 이게 더 중요한데,
        # 프레이밍 판정이 랜드마크 한 벌만 볼 때 동작하므로(coordinator._sense_loop)
        # 뒤에 구경꾼이 한 명만 잡혀도 정위치가 영영 안 뜬다.
        return await emit(EventType.POSITION_REACHED, {State.GUIDING})

    @app.post("/api/debug/ready-signal", include_in_schema=False)
    async def debug_ready_signal() -> dict[str, object]:
        # "손 들어 준비완료"를 운영자가 대신 눌러준다. 타임아웃(12초)을 기다리면
        # 손님은 그냥 서 있는 시간이 되고, 그사이 팔이 언제 움직일지 모른다.
        if not coordinator.force_ready():
            raise HTTPException(
                status_code=409,
                detail="지금은 준비완료 신호를 기다리는 중이 아닙니다",
            )
        return coordinator.context.as_dict()

    @app.post("/api/debug/gesture-left", include_in_schema=False)
    async def debug_gesture_left() -> dict[str, object]:
        """Simulate the completed left-hand hold: full-body framing."""
        return await emit(
            EventType.CAPTURE_STARTED,
            {State.DECIDING},
            template_id="full_body",
        )

    @app.post("/api/debug/gesture-right", include_in_schema=False)
    async def debug_gesture_right() -> dict[str, object]:
        """Simulate the completed right-hand hold: upper-body framing."""
        return await emit(
            EventType.CAPTURE_STARTED,
            {State.DECIDING},
            template_id="upper_body",
        )

    @app.post("/api/debug/start-guide/{template_id}", include_in_schema=False)
    async def debug_start_guide(template_id: str) -> dict[str, object]:
        """Enter the real guide scene without moving a robot.

        Used on the fake/no-robot kiosk configuration to visually verify the
        live iPad overlay. Production still follows the ordinary approach and
        shot-selection flow.
        """
        if template_id not in {"full_body", "upper_body"}:
            raise HTTPException(status_code=422, detail="unknown template_id")
        if coordinator.context.state is State.WAITING:
            revision = coordinator.context.revision
            await coordinator.emit(EventType.PERSON_APPROACHED)
            await coordinator.wait_for_revision(revision + 1)
        if coordinator.context.state is State.GREETING:
            revision = coordinator.context.revision
            await coordinator.emit(EventType.GREETING_DONE)
            await coordinator.wait_for_revision(revision + 1)
        return await emit(
            EventType.CAPTURE_STARTED,
            {State.DECIDING},
            template_id=template_id,
        )

    @app.post("/api/debug/framing/{direction}", include_in_schema=False)
    async def debug_framing(direction: str) -> dict[str, object]:
        """Preview each iPad movement instruction on a fake/no-robot run."""
        messages = {
            "forward": "앞으로 이동하세요",
            "back": "뒤로 이동하세요",
            "left": "화면 왼쪽으로 이동하세요",
            "right": "화면 오른쪽으로 이동하세요",
            "align": "관절을 실루엣 안에 맞춰주세요",
            "hold": "좋습니다 · 그대로 서 주세요",
        }
        if direction not in messages:
            raise HTTPException(status_code=422, detail="unknown framing direction")
        if coordinator.context.state is not State.GUIDING:
            raise HTTPException(status_code=409, detail="framing preview requires guiding state")
        coordinator._framing_debug_until = asyncio.get_running_loop().time() + 5.0
        mode = coordinator._selected_framing_mode()
        if mode in coordinator.framing_templates:
            template = coordinator.framing_templates[mode]
            centers = [band.center for band in template.joints.values()]
            center_x = sum(point.x for point in centers) / len(centers)
            center_y = sum(point.y for point in centers) / len(centers)
            scale = 0.78 if direction == "forward" else 1.24 if direction == "back" else 1.0
            shift_x = -0.08 if direction == "right" else 0.08 if direction == "left" else 0.0
            coordinator._last_framing_points = {
                index: type(band.center)(
                    center_x + (band.center.x - center_x) * scale + shift_x,
                    center_y + (band.center.y - center_y) * scale,
                )
                for index, band in template.joints.items()
            }
        coordinator._patch(
            framing_message=messages[direction],
            framing_direction=direction,
            framing_scale=0.78 if direction == "forward" else 1.24 if direction == "back" else 1.0,
            framing_inside=4 if direction == "hold" else 2,
            framing_required=4,
            framing_positioned=direction == "hold",
        )
        return coordinator.context.as_dict()

    @app.get("/api/qr.svg", include_in_schema=False)
    async def qr_code() -> Response:
        url = coordinator.context.gallery_url
        if not url:
            raise HTTPException(status_code=404, detail="아직 갤러리 링크가 없습니다")
        return Response(
            qr_svg(url),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/events")
    async def events() -> StreamingResponse:
        async def stream():
            async for snapshot in coordinator.updates():
                yield f"event: state\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/fake-photo/{number}.svg", include_in_schema=False)
    async def fake_photo(number: int) -> Response:
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1200" viewBox="0 0 900 1200">
<defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#17213c"/><stop offset="1" stop-color="#4f2b65"/></linearGradient></defs>
<rect width="900" height="1200" fill="url(#g)"/><circle cx="450" cy="390" r="125" fill="#f4c7a1"/>
<path d="M220 1040 Q260 600 450 600 Q640 600 680 1040" fill="#ff725e"/>
<rect x="55" y="55" width="790" height="1090" rx="30" fill="none" stroke="#fff" stroke-width="8" opacity=".7"/>
<text x="450" y="1100" fill="white" font-family="sans-serif" font-size="42" text-anchor="middle">FAKE CAPTURE #{number}</text>
</svg>"""
        return Response(svg, media_type="image/svg+xml")

    return app
