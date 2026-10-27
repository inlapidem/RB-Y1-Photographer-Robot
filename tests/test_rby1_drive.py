from argparse import Namespace

import pytest

from scripts.rby1_drive import validate


def args(**overrides):
    values = dict(
        confirm_drive="MOVE", duration=0.5, forward=0.1, sideways=0.0, turn=0.0, model="a"
    )
    values.update(overrides)
    return Namespace(**values)


def test_drive_requires_explicit_confirmation():
    with pytest.raises(ValueError, match="confirm"):
        validate(args(confirm_drive=""))


def test_drive_rejects_unsafe_duration_and_model_a_lateral_motion():
    with pytest.raises(ValueError, match="1초"):
        validate(args(duration=1.1))
    with pytest.raises(ValueError, match="Model A"):
        validate(args(sideways=0.1))
