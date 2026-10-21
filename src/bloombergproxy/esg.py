"""ESG-Daten mehrerer Anbieter (A3).

Jeder Anbieter liefert ``ESG_SCORE`` auf seiner eigenen Skala (Bloomberg über die Feldliste,
andere per Werte-Import). Für den Vergleich wird jeder Score auf 0–100 umgerechnet, höher =
besser:

* Zahlenskalen: ``(Wert - min) / (max - min) * 100``, bei ``higher_is_better = False`` gespiegelt;
* Buchstabenskalen (z. B. MSCI CCC … AAA): Position in der Liste, gleichmässig verteilt.

Liegen die normierten Scores weiter als ``esg_divergence`` Punkte auseinander, wird das
Instrument als abweichend markiert – ein Hinweis, die Anbieter-Methodik anzusehen.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select

from . import db
from .models import ApiModel, InstrumentStatus
from .service import LicenseService

FIELD = "ESG_SCORE"


class Scale(BaseModel):
    min: float = 0
    max: float = 100
    higher_is_better: bool = True
    letters: list[str] = []  # von schlecht nach gut


class SourceScore(ApiModel):
    source: str
    raw: str
    date: date
    normalized: float | None  # 0–100, höher = besser; None ohne Skala


class EsgSummary(ApiModel):
    instrument_id: str
    name: str | None = None
    scores: list[SourceScore]
    average: float | None
    spread: float | None
    divergent: bool


class Esg:
    def __init__(self, service: LicenseService):
        self.svc = service

    def normalize(self, source: str, raw: Decimal | str) -> float | None:
        cfg = self.svc.settings.esg_scales.get(source)
        if cfg is None:
            return None
        scale = Scale(**cfg)
        if scale.letters:
            text = str(raw).strip().upper()
            if text not in scale.letters:
                return None
            return round(scale.letters.index(text) / (len(scale.letters) - 1) * 100, 1)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        share = (min(max(value, scale.min), scale.max) - scale.min) / (scale.max - scale.min)
        return round((share if scale.higher_is_better else 1 - share) * 100, 1)

    def summary(self, instrument_ids: list[str] | None = None) -> list[EsgSummary]:
        t, i = db.timeseries_value.c, db.instrument.c
        with self.svc.engine.connect() as conn:
            if instrument_ids is None:
                instrument_ids = list(conn.execute(select(i.instrument_id).where(i.status.in_(
                    [InstrumentStatus.ACTIVE, InstrumentStatus.PENDING_UNSUBSCRIBE]))).scalars())
            names = dict(conn.execute(select(i.instrument_id, i.name).where(i.instrument_id.in_(instrument_ids))).all())
            rows = conn.execute(select(t.instrument_id, t.source, t.value_date, t.value_num, t.value_str).where(
                t.instrument_id.in_(instrument_ids), t.field == FIELD, t.superseded_at.is_(None))
                .order_by(t.value_date)).all()
        latest: dict[str, dict[str, tuple]] = {}
        for instrument_id, source, day, num, text in rows:  # aufsteigend: der letzte Wert gewinnt
            latest.setdefault(instrument_id, {})[source] = (day, num if num is not None else text)
        result = []
        for instrument_id in instrument_ids:
            scores = [SourceScore(source=s, raw=format(Decimal(v).normalize(), "f") if isinstance(v, Decimal) else str(v),
                                  date=d, normalized=self.normalize(s, v))
                      for s, (d, v) in sorted(latest.get(instrument_id, {}).items(), key=lambda x: self.svc.source_rank(x[0]))]
            values = [s.normalized for s in scores if s.normalized is not None]
            spread = round(max(values) - min(values), 1) if len(values) >= 2 else None
            result.append(EsgSummary(
                instrument_id=instrument_id, name=names.get(instrument_id), scores=scores,
                average=round(sum(values) / len(values), 1) if values else None, spread=spread,
                divergent=spread is not None and spread > self.svc.settings.esg_divergence))
        return [r for r in result if r.scores]
