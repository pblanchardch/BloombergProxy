"""App-Fabrik: ``uvicorn bloombergproxy.main:create_app --factory``."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .api import router
from .auth import EntraVerifier
from .bloomberg import BloombergClient, MockBloombergClient
from .config import Settings
from .corporate_actions import CorporateActions
from .db import make_engine
from .entitlements import Entitlements
from .quality import DataQuality
from .reconcile import Reconciler
from .vendors import VendorImport
from .service import Conflict, Forbidden, Invalid, LicenseService, NotFound

STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, bloomberg: BloombergClient | None = None,
               service: LicenseService | None = None, verifier: EntraVerifier | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Bloomberg Datenlizenz-Management API", version="0.1.0")
    app.state.settings = settings
    if settings.auth_mode == "entra":
        if not (settings.entra_tenant_id and settings.entra_api_client_id):
            raise ValueError("Für auth_mode=entra fehlen BBGPROXY_ENTRA_TENANT_ID oder BBGPROXY_ENTRA_API_CLIENT_ID")
        app.state.verifier = verifier or EntraVerifier(settings.entra_tenant_id, settings.entra_api_client_id)
    elif settings.auth_mode == "header":
        app.state.verifier = None
    else:
        raise ValueError(f"Unbekannter auth_mode: {settings.auth_mode}")
    app.state.service = service or LicenseService(
        make_engine(settings.database_url), bloomberg or MockBloombergClient(), settings)
    app.state.reconciler = Reconciler(app.state.service)
    app.state.quality = DataQuality(app.state.service)
    app.state.entitlements = Entitlements(app.state.service)
    app.state.vendors = VendorImport(app.state.service)
    app.state.corporate_actions = CorporateActions(app.state.service)
    app.include_router(router)

    @app.middleware("http")
    async def flush_notifications(request: Request, call_next):
        # Meldungen erst nach dem Commit der Anfrage versenden.
        response = await call_next(request)
        if request.method != "GET":
            await run_in_threadpool(app.state.service.notifier.flush)
        return response

    for exc, code in ((NotFound, 404), (Conflict, 409), (Forbidden, 403), (Invalid, 400)):
        def handler(_: Request, e: Exception, code=code, name=exc.__name__):
            return JSONResponse({"code": name, "message": str(e)}, status_code=code)
        app.add_exception_handler(exc, handler)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app

