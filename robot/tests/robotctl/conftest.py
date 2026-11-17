from pathlib import Path

import pytest
from robotctl_helpers import Runner


@pytest.fixture
def run(tmp_path: Path) -> Runner:
    return Runner(tmp_path)
