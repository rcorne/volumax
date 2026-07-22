# Volumax

Cruza las queries reales de **Google Search Console** (qué búsquedas traen tráfico
y a qué página) con los volúmenes de **Keyword Planner** (Google Ads), y sugiere
keywords relacionadas con **mayor** volumen de búsqueda.

Salida: un Excel con dos hojas — `Volúmenes` (con marca de oportunidades
*striking distance*: posición 8–20) y `Sugerencias` (keywords de mayor volumen).

## Instalación

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Uso

### Sin credenciales (modo simulado)

Los volúmenes son ficticios pero el flujo completo funciona — útil para probar:

```bash
./.venv/bin/python volumax.py mi_archivo.xlsx
./.venv/bin/python volumax.py mi_archivo.xlsx --keyword-col "Consulta"
```

Por defecto lee la 2ª columna (B) del archivo.

### Con Google Search Console (gratis, solo OAuth)

1. En [Google Cloud Console](https://console.cloud.google.com/), crea un proyecto
   y habilita **Google Search Console API**.
2. En *Credenciales*, crea un **ID de cliente OAuth** de tipo **Aplicación de
   escritorio** y descarga el JSON como `client_secret.json` en esta carpeta.
3. La primera ejecución abre el navegador para autorizar con la cuenta que tiene
   acceso a la propiedad; el token queda en `token.json`.

```bash
# Ver qué propiedades tienes disponibles
./.venv/bin/python volumax.py --gsc-list-sites

# Analizar un sitio (últimos 90 días, filtrado a Chile)
./.venv/bin/python volumax.py --gsc-site "sc-domain:ejemplo.cl" --country chl

# Opciones útiles
./.venv/bin/python volumax.py --gsc-site "https://www.ejemplo.cl/" \
    --days 180 --min-impressions 50 --limit 500
```

### Con Google Ads / Keyword Planner (volúmenes reales)

Requiere una cuenta de Google Ads activa y un developer token aprobado
(nivel básico basta; la API en sí es gratuita). Configura `google-ads.yaml`
según la [documentación oficial](https://developers.google.com/google-ads/api/docs/get-started/introduction).

```bash
./.venv/bin/python volumax.py --gsc-site "sc-domain:ejemplo.cl" \
    --provider google-ads \
    --customer-id 1234567890 \
    --geo 2152   # 2152=Chile, 2032=Argentina, 2484=México, 2724=España
```

## Credenciales

`client_secret.json`, `token.json` y `google-ads.yaml` están en `.gitignore`
y **nunca deben subirse al repo**.
