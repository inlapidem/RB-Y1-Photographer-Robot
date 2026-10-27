from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from .app import build_coordinator
from .config import AppConfig, load_config
from .workflow import EventType, State

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


async def run_demo(config_path: str) -> None:
    coordinator = build_coordinator(load_config(config_path))
    await coordinator.start()
    print(json.dumps(coordinator.context.as_dict(), ensure_ascii=False))
    await coordinator.emit(EventType.PERSON_APPROACHED)
    await coordinator.wait_for_state(State.GREETING, timeout=5)
    print(json.dumps(coordinator.context.as_dict(), ensure_ascii=False))
    await coordinator.emit(EventType.GREETING_DONE)
    await coordinator.wait_for_state(State.DECIDING, timeout=5)
    await coordinator.emit(EventType.CAPTURE_STARTED, template_id="upper_body")
    await coordinator.wait_for_state(State.GUIDING, timeout=5)
    print(json.dumps(coordinator.context.as_dict(), ensure_ascii=False))
    await coordinator.emit(EventType.POSITION_REACHED)
    await coordinator.wait_for_state(State.PREVIEWING, timeout=5)
    print(json.dumps(coordinator.context.as_dict(), ensure_ascii=False))
    await coordinator.emit(EventType.PREVIEW_DONE)
    await coordinator.wait_for_state(State.ASKING, timeout=5)
    await coordinator.emit(EventType.PHOTO_LIKED)
    await coordinator.wait_for_state(State.FAREWELL, timeout=5)
    await coordinator.emit(EventType.FAREWELL_DONE)
    await coordinator.wait_for_state(State.WAITING, timeout=5)
    print(json.dumps(coordinator.context.as_dict(), ensure_ascii=False))
    await coordinator.stop()


def _launch_debug_window(config: AppConfig) -> subprocess.Popen | None:
    script = SCRIPTS_DIR / "mission_control.py"
    scheme = "https" if config.web.ssl_certfile else "http"
    base_url = f"{scheme}://127.0.0.1:{config.web.port}"
    try:
        return subprocess.Popen([sys.executable, str(script), base_url])
    except OSError as exc:
        print(f"[geekseek] could not launch debug window: {exc}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Geekseek camera robot runtime")
    parser.add_argument("--config", default="config/dev.yaml")
    parser.add_argument("--demo", action="store_true", help="run one headless fake scenario")
    args = parser.parse_args()
    if args.demo:
        asyncio.run(run_demo(args.config))
        return

    import uvicorn

    from .app import build_gallery
    from .gallery import create_gallery_app
    from .web import create_app

    config = load_config(args.config)
    gallery = build_gallery(config)
    debug_window = _launch_debug_window(config) if config.runtime.debug_window else None
    kiosk = uvicorn.Config(
        create_app(build_coordinator(config, gallery)),
        host=config.web.host,
        port=config.web.port,
        log_level="info",
        ssl_keyfile=config.web.ssl_keyfile,
        ssl_certfile=config.web.ssl_certfile,
    )
    try:
        if not gallery.enabled:
            uvicorn.Server(kiosk).run()
            return
        # The guest gallery gets its own plain-HTTP port for a tunnel to
        # publish under a real certificate. It cannot share the kiosk's port:
        # that one serves the phone's camera page, which needs HTTPS, and the
        # kiosk's certificate is self-signed — the exact warning screen the
        # separate origin exists to avoid.
        print(f"[geekseek] gallery: {gallery.base_url} (local :{config.runtime.gallery_port})")
        asyncio.run(
            _serve_both(
                kiosk,
                uvicorn.Config(
                    create_gallery_app(gallery),
                    host="127.0.0.1",
                    port=config.runtime.gallery_port,
                    log_level="warning",
                ),
            )
        )
    finally:
        if debug_window is not None and debug_window.poll() is None:
            debug_window.terminate()


async def _serve_both(*configs) -> None:
    import uvicorn

    servers = [uvicorn.Server(config) for config in configs]
    await asyncio.gather(*(server.serve() for server in servers))


if __name__ == "__main__":
    main()
