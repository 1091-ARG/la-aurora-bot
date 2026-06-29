"""
LA AURORA — BOT DE TELEGRAM
============================
Monitorea medios provinciales, filtra con IA y te manda
las noticias listas para publicar en WordPress.

Cómo funciona:
1. Cada 30 minutos revisa los feeds RSS
2. Claude filtra: ¿es local genuina? ¿vale la pena?
3. Si pasa: Claude la reescribe con voz La Aurora
4. Te llega por Telegram con botones ✅ Publicar / ❌ Descartar
5. Si tocás Publicar, sube directo a WordPress
"""

import os, json, hashlib, asyncio, logging
from datetime import datetime, timedelta
from typing import Optional

import feedparser
import anthropic
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

# ── CREDENCIALES (se cargan desde variables de entorno) ──────────
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "8874805636:AAHpmtZOFDADbGzBHE-8-bpH2e9ArR1yfEE")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "8945845452")
ANTHROPIC_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")  # cargá tu key acá o en env
WP_URL            = os.environ.get("WP_URL", "https://laauroraonline.com")
WP_USER           = os.environ.get("WP_USER", "jmatias.montanez")
WP_APP_PASSWORD   = os.environ.get("WP_APP_PASSWORD", "Z5m0 4ezT oX9m mHiw Iz01 7Brg")

# ── LOGGING ──────────────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s — %(levelname)s — %(message)s',
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ── CLIENTE ANTHROPIC ────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── FEEDS RSS A MONITOREAR ───────────────────────────────────────
# Google News por ciudad = noticias 100% locales
# Medios provinciales directos = cobertura por región

FEEDS = [
    # Por ciudad (lo más limpio, evita noticias nacionales replicadas)
    ("Google·Tartagal",        "https://news.google.com/rss/search?q=%22Tartagal%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Cutral-Có",       "https://news.google.com/rss/search?q=%22Cutral-C%C3%B3%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Oberá",           "https://news.google.com/rss/search?q=%22Ober%C3%A1%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Río Cuarto",      "https://news.google.com/rss/search?q=%22R%C3%ADo+Cuarto%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Comodoro",        "https://news.google.com/rss/search?q=%22Comodoro+Rivadavia%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Venado Tuerto",   "https://news.google.com/rss/search?q=%22Venado+Tuerto%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·San Rafael",      "https://news.google.com/rss/search?q=%22San+Rafael%22+Mendoza&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Concordia",       "https://news.google.com/rss/search?q=%22Concordia%22+Entre+R%C3%ADos&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Resistencia",     "https://news.google.com/rss/search?q=%22Resistencia%22+Chaco&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Posadas",         "https://news.google.com/rss/search?q=%22Posadas%22+Misiones&hl=es-419&gl=AR&ceid=AR:es"),

    # Medios provinciales directos
    ("El Tribuno",             "https://news.google.com/rss/search?q=site:eltribuno.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("La Gaceta",              "https://news.google.com/rss/search?q=site:lagaceta.com.ar&hl=es-419&gl=AR&ceid=AR:es"),
    ("Río Negro",              "https://news.google.com/rss/search?q=site:rionegro.com.ar&hl=es-419&gl=AR&ceid=AR:es"),
    ("Los Andes",              "https://news.google.com/rss/search?q=site:losandes.com.ar&hl=es-419&gl=AR&ceid=AR:es"),
    ("MDZ Online",             "https://news.google.com/rss/search?q=site:mdzol.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("El Territorio",          "https://news.google.com/rss/search?q=site:elterritorio.com.ar&hl=es-419&gl=AR&ceid=AR:es"),
    ("DataChaco",              "https://news.google.com/rss/search?q=site:datachaco.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("LM Neuquén",             "https://news.google.com/rss/search?q=site:lmneuquen.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("ADNSur",                 "https://news.google.com/rss/search?q=site:adnsur.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("0223 Mar del Plata",     "https://news.google.com/rss/search?q=site:0223.com.ar&hl=es-419&gl=AR&ceid=AR:es"),
    ("Rosario3",               "https://news.google.com/rss/search?q=site:rosario3.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("La Nueva Bahía",         "https://news.google.com/rss/search?q=site:lanueva.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("Todo Jujuy",             "https://news.google.com/rss/search?q=site:todojujuy.com&hl=es-419&gl=AR&ceid=AR:es"),
]

# ── MEMORIA DE NOTICIAS YA PROCESADAS ───────────────────────────
# Evita mandarte la misma noticia dos veces
ARCHIVO_VISTOS = "noticias_vistas.json"

def cargar_vistos() -> set:
    try:
        with open(ARCHIVO_VISTOS) as f:
            return set(json.load(f))
    except:
        return set()

def guardar_vistos(vistos: set):
    # Guarda solo los últimos 2000 para no crecer infinito
    lista = list(vistos)[-2000:]
    with open(ARCHIVO_VISTOS, "w") as f:
        json.dump(lista, f)

def id_noticia(titulo: str, link: str) -> str:
    return hashlib.md5(f"{titulo}{link}".encode()).hexdigest()

# ── MEMORIA DE NOTICIAS PENDIENTES DE APROBACIÓN ────────────────
# Guarda las noticias reescritas mientras esperan tu visto bueno
pendientes: dict = {}

# ── PROMPT EDITORIAL ─────────────────────────────────────────────
PROMPT_FILTRO = """Sos el editor de La Aurora, un portal de noticias federal argentino.
Tu trabajo es evaluar si una noticia merece ser publicada.

PUBLICÁ SI:
- El protagonista es un funcionario provincial o municipal (no nacional)
- El hecho ocurrió en una ciudad o localidad específica del interior
- Es un femicidio, desaparición, crimen con posible resonancia nacional
- Es un colapso de servicio público (hospital, agua, luz, ruta)
- Es abuso de poder o corrupción con pruebas concretas
- Es un logro de gestión provincial con datos concretos
- Tiene que ver con Franco Colapinto o Fórmula 1
- Tiene que ver con la Selección argentina o el Mundial

NO PUBLICÁS SI:
- Es la misma noticia que ya publicaron todos los medios nacionales
- El protagonista es Milei, ministros nacionales u otras figuras nacionales sin mención provincial específica
- La fuente original es una agencia nacional (NA, DyN) replicada por el diario provincial
- No menciona una ciudad, municipio o provincia específica en el primer párrafo
- Es un accidente de tránsito sin víctimas fatales
- Es una declaración política sin hechos concretos detrás

Respondé SOLO con JSON, sin explicaciones:
{"publicar": true/false, "razon": "una línea explicando por qué", "region": "NOA/NEA/Cuyo/Litoral/Patagonia/Prov-BsAs/Nacional", "seccion": "politica/economia/actualidad/deportes/opinion"}
"""

PROMPT_REDACCION = """Sos el redactor de La Aurora, un portal de noticias federal argentino.

REGLA DE ORO: La primera línea ya es la noticia. Sin introducción, sin contexto, directo al golpe.

ESTRUCTURA:
- TÍTULO: máximo 12 palabras, verbo activo, sin adornos
- COPETE: 1 oración, máximo 25 palabras, el dato más importante que falta en el título
- CUERPO: 2 párrafos, 4 oraciones cada uno. Párrafo 1: qué/quién/cuándo/dónde. Párrafo 2: contexto mínimo + qué sigue

NUNCA uses: "en el marco de", "cabe destacar", "según pudo saber este medio", voz pasiva innecesaria.

Reescribí esta noticia con esa estructura. Respondé SOLO con JSON:
{"titulo": "...", "copete": "...", "cuerpo": "párrafo1\\n\\npárrafo2"}
"""

# ── FUNCIONES PRINCIPALES ────────────────────────────────────────

def evaluar_noticia(titulo: str, descripcion: str) -> Optional[dict]:
    """Pregunta a Claude si la noticia vale la pena."""
    try:
        msg = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": f"{PROMPT_FILTRO}\n\nTÍTULO: {titulo}\nDESCRIPCIÓN: {descripcion[:500]}"
            }]
        )
        texto = msg.content[0].text.strip()
        # Limpia backticks si los hay
        texto = texto.replace("```json", "").replace("```", "").strip()
        return json.loads(texto)
    except Exception as e:
        log.error(f"Error evaluando noticia: {e}")
        return None

def reescribir_noticia(titulo: str, contenido: str, fuente: str) -> Optional[dict]:
    """Pide a Claude que reescriba la noticia con voz La Aurora."""
    try:
        msg = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": f"{PROMPT_REDACCION}\n\nFUENTE: {fuente}\nTÍTULO ORIGINAL: {titulo}\nCONTENIDO: {contenido[:1000]}"
            }]
        )
        texto = msg.content[0].text.strip()
        texto = texto.replace("```json", "").replace("```", "").strip()
        return json.loads(texto)
    except Exception as e:
        log.error(f"Error reescribiendo noticia: {e}")
        return None

def publicar_en_wordpress(titulo: str, copete: str, cuerpo: str,
                           region: str, seccion: str) -> bool:
    """Publica la nota en WordPress via REST API."""
    try:
        # Mapea región a categoría WP (tenés que crear estas categorías)
        categoria_map = {
            "NOA": "NOA", "NEA": "NEA", "Cuyo": "Cuyo",
            "Litoral": "Litoral", "Patagonia": "Patagonia",
            "Prov-BsAs": "Prov. BsAs", "Nacional": "Actualidad"
        }

        contenido_completo = f"<p><em>{copete}</em></p>\n\n{cuerpo.replace(chr(10), '</p><p>')}"
        contenido_completo = f"<p>{contenido_completo}</p>"

        payload = {
            "title": titulo,
            "content": contenido_completo,
            "excerpt": copete,
            "status": "publish",
        }

        resp = requests.post(
            f"{WP_URL}/wp-json/wp/v2/posts",
            json=payload,
            auth=(WP_USER, WP_APP_PASSWORD),
            timeout=15
        )

        if resp.status_code in [200, 201]:
            log.info(f"✅ Publicado en WP: {titulo}")
            return True
        else:
            log.error(f"Error WP {resp.status_code}: {resp.text[:200]}")
            return False

    except Exception as e:
        log.error(f"Error publicando en WP: {e}")
        return False

async def procesar_feeds(app):
    """Lee todos los feeds y manda las noticias buenas a Telegram."""
    vistos = cargar_vistos()
    nuevas = 0

    for nombre_fuente, url_feed in FEEDS:
        try:
            feed = feedparser.parse(url_feed)
            for entry in feed.entries[:5]:  # máximo 5 por feed
                titulo = entry.get("title", "")
                link = entry.get("link", "")
                descripcion = entry.get("summary", entry.get("description", ""))

                if not titulo or not link:
                    continue

                nid = id_noticia(titulo, link)
                if nid in vistos:
                    continue

                vistos.add(nid)

                # Evalúa con IA
                evaluacion = evaluar_noticia(titulo, descripcion)
                if not evaluacion or not evaluacion.get("publicar"):
                    log.info(f"❌ Descartada: {titulo[:60]}")
                    continue

                # Reescribe con voz La Aurora
                gacetilla = reescribir_noticia(titulo, descripcion, nombre_fuente)
                if not gacetilla:
                    continue

                # Guarda en pendientes
                nid_pendiente = hashlib.md5(gacetilla["titulo"].encode()).hexdigest()[:8]
                pendientes[nid_pendiente] = {
                    "titulo": gacetilla["titulo"],
                    "copete": gacetilla["copete"],
                    "cuerpo": gacetilla["cuerpo"],
                    "region": evaluacion.get("region", "Nacional"),
                    "seccion": evaluacion.get("seccion", "actualidad"),
                    "fuente": nombre_fuente,
                    "link_original": link,
                }

                # Arma el mensaje de Telegram
                region_emoji = {
                    "NOA":"🏔️","NEA":"🌿","Cuyo":"🍇","Litoral":"🌊",
                    "Patagonia":"❄️","Prov-BsAs":"🏙️","Nacional":"🇦🇷"
                }
                emoji = region_emoji.get(evaluacion.get("region",""), "📰")

                mensaje = (
                    f"{emoji} *{evaluacion.get('region','').upper()}* · "
                    f"_{evaluacion.get('seccion','').upper()}_\n\n"
                    f"*{gacetilla['titulo']}*\n\n"
                    f"_{gacetilla['copete']}_\n\n"
                    f"{gacetilla['cuerpo'][:400]}...\n\n"
                    f"📎 Fuente: {nombre_fuente}"
                )

                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Publicar en La Aurora", callback_data=f"pub_{nid_pendiente}"),
                        InlineKeyboardButton("❌ Descartar", callback_data=f"des_{nid_pendiente}"),
                    ]
                ])

                await app.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=mensaje,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                    disable_web_page_preview=True
                )

                nuevas += 1
                await asyncio.sleep(2)  # pausa entre mensajes

        except Exception as e:
            log.error(f"Error procesando feed {nombre_fuente}: {e}")

    guardar_vistos(vistos)
    log.info(f"✅ Ciclo completo — {nuevas} noticias nuevas enviadas")

async def callback_botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja cuando tocás ✅ Publicar o ❌ Descartar."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("pub_"):
        nid = data[4:]
        nota = pendientes.get(nid)
        if nota:
            exito = publicar_en_wordpress(
                nota["titulo"], nota["copete"], nota["cuerpo"],
                nota["region"], nota["seccion"]
            )
            if exito:
                await query.edit_message_text(
                    f"✅ *Publicado en La Aurora*\n\n*{nota['titulo']}*",
                    parse_mode="Markdown"
                )
                del pendientes[nid]
            else:
                await query.edit_message_text("❌ Error al publicar en WordPress. Intentá de nuevo.")
        else:
            await query.edit_message_text("⚠️ Nota no encontrada (puede haber expirado). Esperá el próximo ciclo.")

    elif data.startswith("des_"):
        nid = data[4:]
        if nid in pendientes:
            titulo = pendientes[nid]["titulo"]
            del pendientes[nid]
            await query.edit_message_text(f"🗑️ Descartada: _{titulo}_", parse_mode="Markdown")
        else:
            await query.edit_message_text("🗑️ Descartada.")

async def tarea_periodica(app):
    """Corre el monitoreo cada 30 minutos."""
    while True:
        log.info("🔄 Iniciando ciclo de monitoreo...")
        try:
            await procesar_feeds(app)
        except Exception as e:
            log.error(f"Error en ciclo: {e}")
        await asyncio.sleep(30 * 60)  # 30 minutos

async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CallbackQueryHandler(callback_botones))

    # Mensaje de inicio
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text="🌅 *La Aurora Bot* está activo.\n\nVoy a revisar los medios provinciales cada 30 minutos y te mando lo que vale la pena. Tocás ✅ para publicar o ❌ para descartar.",
        parse_mode="Markdown"
    )

    async with app:
        await app.start()
        await tarea_periodica(app)
        await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
