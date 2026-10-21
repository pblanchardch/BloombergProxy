# BloombergProxy

Lizenz-Service für die Bloomberg Data License: Instrumente bestellen und abbestellen,
Lizenzkosten optimieren, Zeitreihen bereitstellen – über ein Frontend und eine REST-API.

Anforderungskatalog: https://claude.ai/artifact/9GJaqQp9dmCtVSZANLy5cL

## Inhalt

| Pfad | Inhalt |
| --- | --- |
| `api/openapi.yaml` | API-Entwurf (OpenAPI 3.1) |
| `db/ddl.sql` | Datenmodell für Databricks (Unity Catalog, Delta Lake) |

## Eckdaten

- Bloomberg Data License (ohne Data License Plus)
- Positionen aus Xentis, einmal pro Tag
- Zielplattform Databricks (Bronze, Silver, Gold)
- 4-Augen-Freigabe ab 200 CHF Mehrkosten pro Bestellung
