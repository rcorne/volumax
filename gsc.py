# -*- coding: utf-8 -*-
"""
Cliente de Google Search Console (Search Analytics API).

Obtiene las queries que traen tráfico al sitio y a qué página llega cada una.
La API es gratuita; solo requiere OAuth:

  1. En Google Cloud Console, crea un proyecto y habilita
     "Google Search Console API".
  2. Crea credenciales OAuth de tipo "Aplicación de escritorio" y descarga
     el JSON como client_secret.json en esta carpeta.
  3. La primera ejecución abre el navegador para autorizar con la cuenta
     de Google que tiene acceso a la propiedad en Search Console.
     El token queda guardado en token.json para las siguientes ejecuciones.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
ROW_LIMIT = 25000  # máximo de filas por request que permite la API


class SearchConsoleClient:

    def __init__(self, client_secret_path: str = "client_secret.json",
                 token_path: str = "token.json"):
        self.client_secret_path = Path(client_secret_path)
        self.token_path = Path(token_path)
        self.service = self._build_service()

    def _build_service(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.client_secret_path.exists():
                    sys.exit(
                        f"No existe {self.client_secret_path}. Crea credenciales OAuth "
                        "de tipo 'Aplicación de escritorio' en Google Cloud Console "
                        "(con la API de Search Console habilitada) y guarda el JSON "
                        "con ese nombre. Ver README."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.client_secret_path), SCOPES)
                creds = flow.run_local_server(port=0)
            self.token_path.write_text(creds.to_json())
        return build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    def list_sites(self) -> list:
        """Propiedades de Search Console a las que tiene acceso la cuenta."""
        resp = self.service.sites().list().execute()
        return sorted(s["siteUrl"] for s in resp.get("siteEntry", []))

    def top_queries(self, site_url: str, days: int = 90, country: str = None,
                    min_impressions: int = 10, limit: int = 1000) -> pd.DataFrame:
        """Queries con más impresiones en los últimos `days` días.

        Devuelve un DataFrame con: keyword, pagina (la que más clicks recibe
        para esa query), clicks, impresiones y posicion (promedio ponderado
        por impresiones).

        `country` filtra por país en código ISO-3166-1 alpha-3 minúscula
        (ej. "chl" Chile, "arg" Argentina, "mex" México, "esp" España).
        """
        end = date.today() - timedelta(days=2)  # GSC publica con ~2 días de rezago
        start = end - timedelta(days=days)
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query", "page"],
            "rowLimit": ROW_LIMIT,
            "startRow": 0,
        }
        if country:
            body["dimensionFilterGroups"] = [{"filters": [{
                "dimension": "country",
                "operator": "equals",
                "expression": country,
            }]}]

        rows = []
        while True:
            resp = self.service.searchanalytics().query(
                siteUrl=site_url, body=body).execute()
            batch = resp.get("rows", [])
            rows.extend(batch)
            if len(batch) < ROW_LIMIT:
                break
            body["startRow"] += ROW_LIMIT

        if not rows:
            return pd.DataFrame(
                columns=["keyword", "pagina", "clicks", "impresiones", "posicion"])

        df = pd.DataFrame([{
            "keyword": r["keys"][0],
            "pagina": r["keys"][1],
            "clicks": r["clicks"],
            "impresiones": r["impressions"],
            "posicion": r["position"],
        } for r in rows])

        # Una query puede llegar a varias páginas: nos quedamos con la página
        # que más clicks recibe y agregamos las métricas por query.
        top_pages = (df.sort_values("clicks", ascending=False)
                       .drop_duplicates("keyword")[["keyword", "pagina"]])
        df["_pos_pond"] = df["posicion"] * df["impresiones"]
        agg = df.groupby("keyword", as_index=False).agg(
            clicks=("clicks", "sum"),
            impresiones=("impresiones", "sum"),
            _pos_pond=("_pos_pond", "sum"),
        )
        agg["posicion"] = (agg["_pos_pond"] / agg["impresiones"]).round(1)
        out = (agg.drop(columns="_pos_pond")
                  .merge(top_pages, on="keyword")
                  .query("impresiones >= @min_impressions")
                  .sort_values("impresiones", ascending=False)
                  .head(limit)
                  .reset_index(drop=True))
        return out[["keyword", "pagina", "clicks", "impresiones", "posicion"]]
