"""App-Fabrik: ``uvicorn bloombergproxy.main:create_app --factory``."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .bloomberg import BloombergClient, RecordingBloombergClient
from .config import Settings
from .db import make_engine
from .service import Conflict, Forbidden, Invalid, LicenseService, NotFound

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, bloomberg: BloombergClient | None = None,
               service: LicenseService | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Bloomberg Datenlizenz-Management API", version="0.1.0")
    app.state.service = service or LicenseService(
        make_engine(settings.database_url), bloomberg or RecordingBloombergClient(), settings)
    app.include_router(router)

    for exc, code in ((NotFound, 404), (Conflict, 409), (Forbidden, 403), (Invalid, 400)):
        def handler(_: Request, e: Exception, code=code, name=exc.__name__):
            return JSONResponse({"code": name, "message": str(e)}, status_code=code)
        app.add_exception_handler(exc, handler)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app

