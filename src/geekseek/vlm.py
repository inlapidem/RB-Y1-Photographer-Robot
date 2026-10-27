from __future__ import annotations

import base64
from typing import Protocol


class Greeter(Protocol):
    async def greet(self, jpeg: bytes) -> str | None: ...


class FakePersonalizedGreeter:
    """Returns a fixed line (or None to simulate failure), for tests/dev."""

    def __init__(self, line: str | None = "테스트용 인사말입니다") -> None:
        self.line = line

    async def greet(self, jpeg: bytes) -> str | None:
        return self.line


_PROMPT = (
    "사진 로봇이 방금 카메라에 잡힌 손님(들)에게 건넬 반갑고 짧은 한국어 인사말을 "
    "딱 한 문장, 20자 내외로 만들어줘. 외모나 신체 특징은 언급하지 말고, 옷 색이나 "
    "분위기, 함께 있는 인원 수 정도만 참고해서 밝고 친근한 톤으로 만들어줘. "
    "문장만 출력하고 다른 말은 붙이지 마."
)


class ClaudeGreeter:
    """Personalizes the 2단계(greeting) caption from the webcam frame at the
    moment a person is detected. Fired in the background by the coordinator —
    never on the hot path. Any failure (no API key, refusal, timeout, network
    error) returns None so the caller falls back to the static caption;
    this must never block or break the workflow."""

    def __init__(self, model: str = "claude-opus-5", timeout_seconds: float = 4.0) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic()
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def greet(self, jpeg: bytes) -> str | None:
        import asyncio

        image_b64 = base64.standard_b64encode(jpeg).decode("utf-8")
        try:
            response = await asyncio.wait_for(
                self._client.messages.create(
                    model=self._model,
                    max_tokens=100,
                    thinking={"type": "disabled"},
                    output_config={"effort": "low"},
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": image_b64,
                                    },
                                },
                                {"type": "text", "text": _PROMPT},
                            ],
                        }
                    ],
                ),
                timeout=self._timeout_seconds,
            )
        except Exception:
            return None

        if response.stop_reason == "refusal":
            return None
        text = next((block.text for block in response.content if block.type == "text"), None)
        return text.strip() if text else None
