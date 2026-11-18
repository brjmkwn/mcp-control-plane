"""AWS Lambda Mangum adapter for serverless container deployment."""

from __future__ import annotations

from mangum import Mangum

from mcp_control_plane.config import get_settings
from mcp_control_plane.main import create_app

app = create_app(get_settings())
handler = Mangum(app, lifespan="off")
