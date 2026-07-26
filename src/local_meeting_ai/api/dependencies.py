from __future__ import annotations

from typing import cast

from fastapi import Request

from local_meeting_ai.bootstrap import Container


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)
