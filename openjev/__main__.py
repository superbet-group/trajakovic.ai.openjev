import logging
import os

import uvicorn

from .api import create_app

# OPENJEV_LOG_LEVEL: info (default) logs one line per request; debug adds the request
# body (images redacted) and the response body. uvicorn stays at info under debug: its
# own debug output is connection-level noise.
level = os.environ.get("OPENJEV_LOG_LEVEL", "info").lower()
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s"))
app_log = logging.getLogger("openjev")
app_log.addHandler(handler)
app_log.setLevel(level.upper())
app_log.propagate = False

uvicorn.run(create_app(), host=os.environ.get("OPENJEV_HOST", "127.0.0.1"), port=int(os.environ.get("OPENJEV_PORT", "8080")),
            log_level="info" if level == "debug" else level)
