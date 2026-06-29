"""
LA AURORA — BOT DE TELEGRAM (webhook version)
Monitorea medios provinciales, filtra con IA,
te manda las noticias por Telegram con botones
✅ Publicar / ❌ Descartar → publica en WordPress.
"""

import os, json, hashlib, asyncio, logging, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

import feedparser
import anthropic
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

# ── CREDENCIALES ─────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
WP_URL           = os.environ.get("WP_URL", "https://laauroraonline.com")
WP_USER          = os.environ.get("WP_USER", "jmatias.montanez")
WP_APP_PASSWORD  = os.environ.get("WP_APP_PASSWORD", "")
WEBHOOK_URL      = os.environ.get("WEBHOOK_URL", "")  # se carga después
PORT             = int(os.environ.get("PORT", 8080))

# ── LOGGING ──────────────────────────────────────────────────────
logging.basicConfig(format='%(asctime)s — %(levelname)s — %(message)s', level=logging.INFO)
log = logging.getLogger(__name__)

# ── CLIENTE ANTHROPIC ────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── FEEDS ────────────────────────────────────────────────────────
FEEDS = [
    ("Google·Tartagal",      "https://news.google.com/rss/search?q=%22Tartagal%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Cutral-Có",     "https://news.google.com/rss/search?q=%22Cutral-C%C3%B3%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Oberá",         "https://news.google.com/rss/search?q=%22Ober%C3%A1%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Río Cuarto",    "https://news.google.com/rss/search?q=%22R%C3%ADo+Cuarto%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Comodoro",      "https://news.google.com/rss/search?q=%22Comodoro+Rivadavia%22&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Concordia",     "https://news.google.com/rss/search?q=%22Concordia%22+Entre+R%C3%ADos&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Resistencia",   "https://news.google.com/rss/search?q=%22Resistencia%22+Chaco&hl=es-419&gl=AR&ceid=AR:es"),
    ("Google·Posadas",       "https://news.google.com/rss/search?q=%22Posadas%22+Misiones&hl=es-419&gl=AR&ceid=AR:es"),
    ("El Tribuno",           "https://news.google.com/rss/search?q=site:eltribuno.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("La Gaceta",            "https://news.google.com/rss/search?q=site:lagaceta.com.ar&hl=es-419&gl=AR&ceid=AR:es"),
    ("Río Negro",            "https://news.google.com/rss/search?q=site:rionegro.com.ar&hl=es-419&gl=AR&ceid=AR:es"),
    ("Los Andes",            "https://news.google.com/rss/search?q=site:losandes.com.ar&hl=es-419&gl=AR&ceid=AR:es"),
    ("MDZ Online",           "https://news.google.com/rss/search?q=site:mdzol.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("El Territorio",        "https://news.google.com/rss/search?q=site:elterritorio.com.ar&hl=es-419&gl=AR&ceid=AR:es"),
    ("DataChaco",            "https://news.google.com/rss/search?q=site:datachaco.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("LM Neuquén",           "https://news.google.com/rss/search?q=site:lmneuquen.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("ADNSur",               "https://news.google.com/rss/search?q=site:adnsur.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("0223",                 "https://news.google.com/rss/search?q=site:0223.com.ar&hl=es-419&gl=AR&ceid=AR:es"),
    ("Rosario3",             "https://news.google.com/rss/search?q=site:rosario3.com&hl=es-419&gl=AR&ceid=AR:es"),
    ("Todo Jujuy",           "https://news.google.com/rss/search?q=site:todojujuy.com&hl=es-419&gl=AR&ceid=AR:es"),
]

# ── MEMORIA ───────────────────────────────────────────────────────
ARCHIVO_VISTOS = "noticias_vistas.json"
pendientes: dict = {}

def cargar_vistos() -> set:
    try:
        with open(ARCHIVO_VISTOS) as f: return set(json.load(f))
    except: return set()

def guardar_vistos(v: set):
    with open(ARCHIVO_VISTOS, "w") as f: json.dump(list(v)[-2000:], f)

def nid(titulo, link): return hashlib.md5(f"{titulo}{link}".encode()).hexdigest()

# ── PROMPTS ───────────────────────────────────────────────────────
FILTRO = """Sos el editor de La Aurora, portal federal argentino.
Evaluá si esta noticia merece publicarse.

PUBLICÁ SI: funcionario provincial/municipal implicado, hecho en ciudad específica del interior,
femicidio/desaparición/crimen resonante, colapso de servicio público, abuso de poder con pruebas,
gestión provincial con datos, Colapinto/F1, Selección/Mundial.

NO PUBLICÁS SI: misma nota en todos los medios nacionales, protagonista es Milei/ministros sin mención provincial,
fuente es agencia nacional replicada, no menciona ciudad/provincia en el primer párrafo, accidente sin víctimas fatales.

Respondé SOLO JSON sin explicaciones:
{"publicar": true/false, "razon": "una línea", "region": "NOA/NEA/Cuyo/Litoral/Patagonia/Prov-BsAs/Nacional", "seccion": "politica/economia/actualidad/deportes/opinion"}"""

REDACCION = """Sos el redactor de La Aurora. REGLA: la primera línea ya es la noticia. Sin introducción.

ESTRUCTURA:
- TÍTULO: máximo 12 palabras, verbo activo
- COPETE: 1 oración, máximo 25 palabras
- CUERPO: 2 párrafos de 4 oraciones. Párrafo 1: qué/quién/cuándo/dónde. Párrafo 2: contexto + qué sigue.

NUNCA uses: "en el marco de", "cabe destacar", voz pasiva innecesaria.

Respondé SOLO JSON:
{"titulo": "...", "copete": "...", "cuerpo": "párrafo1\\n\\npárrafo2"}"""

# ── IA ────────────────────────────────────────────────────────────
def evaluar(titulo, desc):
    try:
        r = claude.messages.create(model="claude-sonnet-4-6", max_tokens=300,
            messages=[{"role":"user","content":f"{FILTRO}\n\nTÍTULO: {titulo}\nDESCRIPCIÓN: {desc[:500]}"}])
        return json.loads(r.content[0].text.strip().replace("```json","").replace("```","").strip())
    except Exception as e:
        log.error(f"Error evaluando: {e}"); return None

def reescribir(titulo, contenido, fuente):
    try:
        r = claude.messages.create(model="claude-sonnet-4-6", max_tokens=800,
            messages=[{"role":"user","content":f"{REDACCION}\n\nFUENTE: {fuente}\nTÍTULO: {titulo}\nCONTENIDO: {contenido[:1000]}"}])
        return json.loads(r.content[0].text.strip().replace("```json","").replace("```","").strip())
    except Exception as e:
        log.error(f"Error reescribiendo: {e}"); return None

# ── WORDPRESS ─────────────────────────────────────────────────────
def publicar_wp(titulo, copete, cuerpo, region, seccion):
    try:
        contenido = f"<p><em>{copete}</em></p>\n\n<p>{cuerpo.replace(chr(10)+chr(10), '</p><p>')}</p>"
        r = requests.post(f"{WP_URL}/wp-json/wp/v2/posts",
            json={"title": titulo, "content": contenido, "excerpt": copete, "status": "publish"},
            auth=(WP_USER, WP_APP_PASSWORD), timeout=15)
        if r.status_code in [200, 201]:
            log.info(f"✅ Publicado: {titulo}"); return True
        log.error(f"WP error {r.status_code}: {r.text[:200]}"); return False
    except Exception as e:
        log.error(f"Error WP: {e}"); return False

# ── MONITOREO ─────────────────────────────────────────────────────
async def procesar_feeds(app):
    vistos = cargar_vistos()
    nuevas = 0
    emojis = {"NOA":"🏔️","NEA":"🌿","Cuyo":"🍇","Litoral":"🌊","Patagonia":"❄️","Prov-BsAs":"🏙️","Nacional":"🇦🇷"}

    for fuente, url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:5]:
                titulo = e.get("title","")
                link = e.get("link","")
                desc = e.get("summary", e.get("description",""))
                if not titulo or not link: continue
                n = nid(titulo, link)
                if n in vistos: continue
                vistos.add(n)

                ev = evaluar(titulo, desc)
                if not ev or not ev.get("publicar"):
                    log.info(f"❌ {titulo[:60]}"); continue

                g = reescribir(titulo, desc, fuente)
                if not g: continue

                pid = hashlib.md5(g["titulo"].encode()).hexdigest()[:8]
                pendientes[pid] = {"titulo":g["titulo"],"copete":g["copete"],"cuerpo":g["cuerpo"],
                    "region":ev.get("region","Nacional"),"seccion":ev.get("seccion","actualidad"),
                    "fuente":fuente,"link":link}

                emoji = emojis.get(ev.get("region",""), "📰")
                msg = (f"{emoji} *{ev.get('region','').upper()}* · _{ev.get('seccion','').upper()}_\n\n"
                       f"*{g['titulo']}*\n\n_{g['copete']}_\n\n{g['cuerpo'][:400]}...\n\n📎 Fuente: {fuente}")

                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Publicar en La Aurora", callback_data=f"pub_{pid}"),
                    InlineKeyboardButton("❌ Descartar", callback_data=f"des_{pid}"),
                ]])

                await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg,
                    parse_mode="Markdown", reply_markup=kb, disable_web_page_preview=True)
                nuevas += 1
                await asyncio.sleep(2)

        except Exception as e:
            log.error(f"Error feed {fuente}: {e}")

    guardar_vistos(vistos)
    log.info(f"✅ Ciclo completo — {nuevas} noticias nuevas")

# ── CALLBACK BOTONES ──────────────────────────────────────────────
async def callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("pub_"):
        pid = data[4:]
        nota = pendientes.get(pid)
        if nota:
            ok = publicar_wp(nota["titulo"], nota["copete"], nota["cuerpo"], nota["region"], nota["seccion"])
            if ok:
                await query.edit_message_text(f"✅ *Publicado en La Aurora*\n\n*{nota['titulo']}*", parse_mode="Markdown")
                del pendientes[pid]
            else:
                await query.edit_message_text("❌ Error al publicar en WordPress.")
        else:
            await query.edit_message_text("⚠️ Nota expirada. Esperá el próximo ciclo.")

    elif data.startswith("des_"):
        pid = data[4:]
        titulo = pendientes.get(pid, {}).get("titulo", "")
        if pid in pendientes: del pendientes[pid]
        await query.edit_message_text(f"🗑️ Descartada: _{titulo}_", parse_mode="Markdown")

# ── TAREA PERIÓDICA ───────────────────────────────────────────────
async def tarea_periodica(app):
    while True:
        log.info("🔄 Iniciando ciclo...")
        try: await procesar_feeds(app)
        except Exception as e: log.error(f"Error ciclo: {e}")
        await asyncio.sleep(30 * 60)

# ── HEALTH CHECK (para Railway) ───────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass

def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

# ── MAIN ──────────────────────────────────────────────────────────
async def main():
    # Servidor de health check en thread separado
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()
    log.info(f"Health server en puerto {PORT}")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CallbackQueryHandler(callback))

    # Configurar webhook
    webhook = WEBHOOK_URL or ""
    if webhook:
        await app.bot.set_webhook(url=f"{webhook}/webhook")
        log.info(f"Webhook configurado: {webhook}/webhook")

        async with app:
            await app.start()
            await app.updater.start_webhook(
                listen="0.0.0.0",
                port=PORT + 1,
                url_path="webhook",
                webhook_url=f"{webhook}/webhook"
            )
            await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID,
                text="🌅 *La Aurora Bot* activo con webhook.\n\nRevisaré los medios cada 30 minutos.",
                parse_mode="Markdown")
            await tarea_periodica(app)
    else:
        # Fallback a polling si no hay webhook URL
        log.info("Sin WEBHOOK_URL — usando polling")
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID,
                text="🌅 *La Aurora Bot* activo.\n\nRevisaré los medios cada 30 minutos.",
                parse_mode="Markdown")
            await tarea_periodica(app)

if __name__ == "__main__":
    asyncio.run(main())
