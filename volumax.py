# -*- coding: utf-8 -*-
"""
Volumax — volúmenes de búsqueda y sugerencias de keywords de mayor volumen.

Flujo:
  1. Obtiene keywords desde un Excel/CSV o directamente desde Google Search
     Console (queries reales que traen tráfico al sitio, con su página).
  2. Obtiene el volumen de búsqueda mensual promedio de cada keyword.
  3. Pide ideas de keywords relacionadas y sugiere las que tienen MÁS volumen
     que la keyword original.
  4. Escribe un Excel con dos hojas: "Volúmenes" y "Sugerencias".

Fuente de keywords:
  entrada.xlsx / entrada.csv     archivo local (por defecto la 2ª columna)
  --gsc-site <propiedad>         Search Console, ej. "sc-domain:ejemplo.cl"
                                 o "https://www.ejemplo.cl/" (requiere OAuth, gratis)
  --gsc-list-sites               lista las propiedades disponibles y termina

Proveedores de volumen:
  --provider mock        (por defecto) datos simulados, no requiere APIs ni credenciales.
  --provider google-ads  usa la API real de Google Ads / Keyword Planner.
                         Requiere google-ads.yaml y developer token aprobado.

Uso:
  python volumax.py entrada.xlsx
  python volumax.py entrada.xlsx -o salida.xlsx --geo 2152 --keyword-col "Consulta"
  python volumax.py --gsc-site "sc-domain:ejemplo.cl" --country chl
  python volumax.py --gsc-site "sc-domain:ejemplo.cl" --provider google-ads --customer-id 1234567890
"""

import argparse
import hashlib
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class KeywordMetrics:
    keyword: str
    avg_monthly_searches: Optional[int] = None
    competition: Optional[str] = None  # LOW / MEDIUM / HIGH


@dataclass
class Suggestion:
    original: str
    suggestion: str
    avg_monthly_searches: int
    uplift: float  # cuántas veces más volumen que la original (ej. 3.5x)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Normaliza para comparar keywords: minúsculas, sin tildes, espacios simples."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def chunks(items: List[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------------------------------------------------------------------------
# Proveedor simulado (sin APIs)
# ---------------------------------------------------------------------------

MODIFIERS_ES = [
    ("{kw} precio", 2.8),
    ("{kw} online", 2.2),
    ("mejor {kw}", 1.9),
    ("{kw} cerca de mi", 1.6),
    ("{kw} barato", 1.4),
    ("como elegir {kw}", 0.6),
    ("{kw} opiniones", 0.9),
    ("{kw} 2026", 0.7),
]


class MockProvider:
    """Genera datos deterministas (misma keyword → mismo volumen) para poder
    desarrollar y probar el flujo completo sin acceso a las APIs."""

    name = "mock"

    def _base_volume(self, keyword: str) -> int:
        h = int(hashlib.md5(normalize(keyword).encode()).hexdigest(), 16)
        # Distribución tipo long-tail: mayoría bajo, algunas altas
        bucket = h % 100
        if bucket < 60:
            vol = 10 + h % 490          # 10 – 500
        elif bucket < 90:
            vol = 500 + h % 4500        # 500 – 5.000
        else:
            vol = 5000 + h % 45000      # 5.000 – 50.000
        return int(round(vol, -1))

    def historical_metrics(self, keywords: List[str]) -> Dict[str, KeywordMetrics]:
        out = {}
        for kw in keywords:
            vol = self._base_volume(kw)
            comp = ["LOW", "MEDIUM", "HIGH"][vol % 3]
            out[normalize(kw)] = KeywordMetrics(kw, vol, comp)
        return out

    def keyword_ideas(self, seed: str) -> List[KeywordMetrics]:
        ideas = []
        for template, factor in MODIFIERS_ES:
            idea = template.format(kw=normalize(seed))
            h = int(hashlib.md5(idea.encode()).hexdigest(), 16)
            jitter = 0.7 + (h % 60) / 100          # 0.7 – 1.3
            vol = int(round(self._base_volume(seed) * factor * jitter, -1))
            ideas.append(KeywordMetrics(idea, max(vol, 10)))
        return ideas


# ---------------------------------------------------------------------------
# Proveedor real: Google Ads / Keyword Planner
# (código corregido respecto al original; requiere credenciales)
# ---------------------------------------------------------------------------

class GoogleAdsProvider:
    name = "google-ads"
    BATCH_SIZE = 1000  # la API acepta hasta 10.000 keywords por request

    def __init__(self, credentials_path: str, customer_id: str,
                 language: str = "languageConstants/1003",
                 geo_targets: Optional[List[str]] = None):
        from google.ads.googleads.client import GoogleAdsClient
        self.client = GoogleAdsClient.load_from_storage(credentials_path)
        self.customer_id = customer_id
        self.language = language
        self.geo_targets = geo_targets or ["geoTargetConstants/2152"]  # 2152 = Chile
        self.service = self.client.get_service("KeywordPlanIdeaService")

    def historical_metrics(self, keywords: List[str]) -> Dict[str, KeywordMetrics]:
        from google.ads.googleads.errors import GoogleAdsException
        out: Dict[str, KeywordMetrics] = {}
        for batch in chunks(keywords, self.BATCH_SIZE):
            request = self.client.get_type("GenerateKeywordHistoricalMetricsRequest")
            request.customer_id = self.customer_id
            request.keywords.extend(batch)
            request.language = self.language
            request.geo_target_constants.extend(self.geo_targets)
            try:
                response = self.service.generate_keyword_historical_metrics(request=request)
            except GoogleAdsException as ex:
                print(f"Error de Google Ads: {ex.failure}", file=sys.stderr)
                continue
            # BUG corregido del original: los resultados vienen en response.results
            # (no response.close_variants) y la keyword está en result.text
            # (no result.search_query).
            for result in response.results:
                m = result.keyword_metrics
                out[normalize(result.text)] = KeywordMetrics(
                    keyword=result.text,
                    avg_monthly_searches=m.avg_monthly_searches if m else None,
                    competition=m.competition.name if m else None,
                )
                # Google agrupa variantes cercanas ("zapatilla"/"zapatillas");
                # las mapeamos también para no perder coincidencias.
                for variant in result.close_variants:
                    out.setdefault(normalize(variant), out[normalize(result.text)])
        return out

    def keyword_ideas(self, seed: str) -> List[KeywordMetrics]:
        from google.ads.googleads.errors import GoogleAdsException
        request = self.client.get_type("GenerateKeywordIdeasRequest")
        request.customer_id = self.customer_id
        request.language = self.language
        request.geo_target_constants.extend(self.geo_targets)
        request.keyword_seed.keywords.append(seed)
        try:
            response = self.service.generate_keyword_ideas(request=request)
        except GoogleAdsException as ex:
            print(f"Error pidiendo ideas para '{seed}': {ex.failure}", file=sys.stderr)
            return []
        ideas = []
        for idea in response:
            m = idea.keyword_idea_metrics
            if m and m.avg_monthly_searches:
                ideas.append(KeywordMetrics(idea.text, m.avg_monthly_searches,
                                            m.competition.name))
        return ideas


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def load_keywords(path: Path, column: Optional[str]) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)
    if column:
        if column not in df.columns:
            sys.exit(f"La columna '{column}' no existe. Columnas disponibles: {list(df.columns)}")
        df = df.rename(columns={column: "keyword"})
    else:
        # Igual que el script original: 2ª columna (B) si existe, si no la 1ª
        idx = 1 if df.shape[1] > 1 else 0
        df = df.rename(columns={df.columns[idx]: "keyword"})
    df = df[["keyword"]].dropna().drop_duplicates().reset_index(drop=True)
    df["keyword"] = df["keyword"].astype(str).str.strip()
    return df[df["keyword"] != ""]


def build_report(df: pd.DataFrame, provider, top_n: int = 5,
                 min_uplift: float = 1.2) -> tuple:
    keywords = df["keyword"].tolist()
    print(f"Consultando volúmenes de {len(keywords)} keywords (proveedor: {provider.name})...")
    metrics = provider.historical_metrics(keywords)

    df["volumen_mensual"] = [
        metrics.get(normalize(kw)).avg_monthly_searches if normalize(kw) in metrics else None
        for kw in keywords
    ]
    df["competencia"] = [
        metrics.get(normalize(kw)).competition if normalize(kw) in metrics else None
        for kw in keywords
    ]

    print("Buscando sugerencias de mayor volumen...")
    suggestions: List[Suggestion] = []
    for kw in keywords:
        base = metrics.get(normalize(kw))
        base_vol = base.avg_monthly_searches if base else None
        if not base_vol:
            continue
        seen = {normalize(kw)}
        ideas = [i for i in provider.keyword_ideas(kw)
                 if i.avg_monthly_searches
                 and i.avg_monthly_searches >= base_vol * min_uplift
                 and normalize(i.keyword) not in seen]
        ideas.sort(key=lambda i: i.avg_monthly_searches, reverse=True)
        for idea in ideas[:top_n]:
            seen.add(normalize(idea.keyword))
            suggestions.append(Suggestion(
                original=kw,
                suggestion=idea.keyword,
                avg_monthly_searches=idea.avg_monthly_searches,
                uplift=round(idea.avg_monthly_searches / base_vol, 1),
            ))

    sug_df = pd.DataFrame(
        [(s.original, s.suggestion, s.avg_monthly_searches, f"{s.uplift}x")
         for s in suggestions],
        columns=["keyword_original", "sugerencia", "volumen_mensual", "mejora"],
    )
    return df, sug_df


def main():
    parser = argparse.ArgumentParser(description="Volúmenes y sugerencias de keywords")
    parser.add_argument("input", type=Path, nargs="?", default=None,
                        help="Excel o CSV con las keywords (omitir si usas --gsc-site)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Excel de salida (por defecto <input>_volumax.xlsx)")
    parser.add_argument("--keyword-col", default=None,
                        help="Nombre de la columna con keywords (por defecto la 2ª columna)")
    parser.add_argument("--provider", choices=["mock", "google-ads"], default="mock")
    parser.add_argument("--credentials", default="google-ads.yaml",
                        help="Ruta a google-ads.yaml (solo provider google-ads)")
    parser.add_argument("--customer-id", default=None,
                        help="ID de cliente de Google Ads sin guiones")
    parser.add_argument("--geo", default="2152",
                        help="geoTargetConstant (2152=Chile, 2032=Argentina, 2484=México, 2724=España)")
    parser.add_argument("--language", default="1003",
                        help="languageConstant (1003=Español)")
    parser.add_argument("--top", type=int, default=5,
                        help="Máximo de sugerencias por keyword (default 5)")
    parser.add_argument("--min-uplift", type=float, default=1.2,
                        help="Mínimo multiplicador de volumen para sugerir (default 1.2)")
    # Opciones de Google Search Console
    parser.add_argument("--gsc-site", default=None,
                        help='Propiedad de Search Console, ej. "sc-domain:ejemplo.cl"')
    parser.add_argument("--gsc-list-sites", action="store_true",
                        help="Lista las propiedades de Search Console disponibles y termina")
    parser.add_argument("--gsc-credentials", default="client_secret.json",
                        help="JSON de OAuth de Google Cloud (default client_secret.json)")
    parser.add_argument("--days", type=int, default=90,
                        help="Días hacia atrás a consultar en GSC (default 90)")
    parser.add_argument("--country", default=None,
                        help="Filtro de país ISO-3166 alpha-3 en GSC (chl, arg, mex, esp)")
    parser.add_argument("--min-impressions", type=int, default=10,
                        help="Mínimo de impresiones en GSC para incluir la query (default 10)")
    parser.add_argument("--limit", type=int, default=1000,
                        help="Máximo de queries a traer de GSC (default 1000)")
    args = parser.parse_args()

    if args.gsc_list_sites:
        from gsc import SearchConsoleClient
        client = SearchConsoleClient(args.gsc_credentials)
        print("Propiedades disponibles en Search Console:")
        for site in client.list_sites():
            print(f"  {site}")
        return

    if not args.input and not args.gsc_site:
        sys.exit("Indica un archivo de entrada o una propiedad con --gsc-site. "
                 "Usa --gsc-list-sites para ver tus propiedades.")
    if args.input and not args.input.exists():
        sys.exit(f"No existe el archivo: {args.input}")

    if args.provider == "google-ads":
        if not args.customer_id:
            sys.exit("--customer-id es obligatorio con --provider google-ads")
        provider = GoogleAdsProvider(
            credentials_path=args.credentials,
            customer_id=args.customer_id,
            language=f"languageConstants/{args.language}",
            geo_targets=[f"geoTargetConstants/{args.geo}"],
        )
    else:
        provider = MockProvider()
        print("⚠ Modo simulado: los volúmenes son ficticios (sin acceso a APIs).")

    if args.gsc_site:
        from gsc import SearchConsoleClient
        client = SearchConsoleClient(args.gsc_credentials)
        print(f"Consultando Search Console: {args.gsc_site} "
              f"(últimos {args.days} días{', país ' + args.country if args.country else ''})...")
        df = client.top_queries(args.gsc_site, days=args.days, country=args.country,
                                min_impressions=args.min_impressions, limit=args.limit)
        if df.empty:
            sys.exit("Search Console no devolvió queries con esos filtros.")
        print(f"  {len(df)} queries obtenidas.")
    else:
        df = load_keywords(args.input, args.keyword_col)

    vol_df, sug_df = build_report(df, provider, top_n=args.top, min_uplift=args.min_uplift)

    # Con datos de GSC podemos marcar oportunidades "striking distance":
    # queries en posición 8-20, donde subir posiciones rinde más tráfico.
    if "posicion" in vol_df.columns:
        vol_df["oportunidad"] = vol_df["posicion"].between(8, 20).map(
            {True: "striking distance", False: ""})

    default_name = (Path(f"{args.gsc_site.replace('sc-domain:', '').replace('https://', '').strip('/').replace('/', '_')}_volumax.xlsx")
                    if args.gsc_site else
                    args.input.with_name(args.input.stem + "_volumax.xlsx"))
    output = args.output or default_name
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        vol_df.to_excel(writer, sheet_name="Volúmenes", index=False)
        sug_df.to_excel(writer, sheet_name="Sugerencias", index=False)

    print(f"\nListo: {output}")
    print(f"  - Volúmenes: {len(vol_df)} keywords")
    print(f"  - Sugerencias: {len(sug_df)} keywords con mayor volumen")


if __name__ == "__main__":
    main()
