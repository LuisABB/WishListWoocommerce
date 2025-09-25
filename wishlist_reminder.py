# -*- coding: utf-8 -*-
"""
wishlist_reminder.py
Worker para enviar recordatorios por wishlist (24/48/72h).

Características:
- Ventanas relativas (TARGET_HOURS ± WINDOW_TOLERANCE_H) o modo "8:00 am fijo" por fecha local (CDMX).
- Bloqueo permanente por campaign_key: si ya se envió esa campaign_key alguna vez a ese email+wishlist,
  NO se vuelve a enviar (independiente de COOLDOWN_HOURS).
- Requiere que la wishlist tenga al menos 1 item.
- Render de plantilla HTML con grid básico de productos y link a la wishlist.
"""

import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr, make_msgid
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import urlparse

# DB: mysql-connector-python (con socket)
import mysql.connector as mysql

# Carga .env ubicado junto al script
ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(ENV_PATH)

# -----------------------
# Utilidades de consola
# -----------------------

def logsafe(s):
    """Evita UnicodeEncodeError en consolas no-UTF8 (Windows cp1252, etc.)."""
    try:
        enc = (sys.stdout.encoding or 'ascii')
        return str(s).encode(enc, 'replace').decode(enc, 'replace')
    except Exception:
        return str(s).encode('ascii', 'replace').decode('ascii', 'replace')


def _now_utc():
    return datetime.now(timezone.utc)

# -----------------------
# Configuración (ENV)
# -----------------------

DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "relojesc_relob")
DB_SOCKET = os.getenv("DB_SOCKET")  # /var/lib/mysql/mysql.sock
TABLE_PREFIX = os.getenv("TABLE_PREFIX", "wp_")

# SMTP
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").strip().lower() == "true"

FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER or "no-reply@example.com")
FROM_NAME  = os.getenv("FROM_NAME", "Relojes Curren México")
REPLY_TO   = os.getenv("REPLY_TO", FROM_EMAIL)

# Control de campaña/etapa
STAGE = int(os.getenv("STAGE", "24"))  # 24 / 48 / 72
TARGET_HOURS = int(os.getenv("TARGET_HOURS", str(STAGE)))
WINDOW_TOLERANCE_H = int(os.getenv("WINDOW_TOLERANCE_H", "6"))
CAMPAIGN_KEY = os.getenv("CAMPAIGN_KEY", f"wishlist_v1_{STAGE}h")
COOLDOWN_HOURS = int(os.getenv("COOLDOWN_HOURS", "168"))  # 7 días (ya no se usa para filtrar, queda por compatibilidad)

MAX_BATCH = int(os.getenv("MAX_BATCH", "300"))
SEND_EMAILS = os.getenv("SEND_EMAILS", "false").strip().lower() == "true"

# Modo “8:00 am fijo”
FIXED_8AM_MODE = os.getenv("FIXED_8AM_MODE", "false").strip().lower() == "true"
LOCAL_TZ_OFFSET = os.getenv("LOCAL_TZ_OFFSET", "-06:00")  # America/Mexico_City fijo con offset

# Plantilla / asunto
TEMPLATE_FILE = os.getenv("TEMPLATE_FILE", "templates/wishlist_email_24h.html")
SUBJECT = os.getenv("SUBJECT", "Tu reloj favorito te espera ⌚")

# Base URL de la tienda / wishlist (obligatorio para links)
WISHLIST_URL = os.getenv("WISHLIST_URL", "").strip()
if not WISHLIST_URL:
    raise RuntimeError("Falta WISHLIST_URL en el .env (p. ej. https://www.relojescurrenmexico.com.mx)")

# Placeholder para imágenes faltantes
PLACEHOLDER_IMG = os.getenv("PLACEHOLDER_IMG", "https://via.placeholder.com/300x300?text=Producto")

# -----------------------
# MySQL helpers
# -----------------------

def mysql_conn():
    conn = mysql.connect(
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        unix_socket=DB_SOCKET,   # usar socket como en el test OK
        autocommit=True
    )
    with conn.cursor(dictionary=True) as cur:
        # Normaliza zona del lado servidor a UTC
        cur.execute("SET time_zone = '+00:00'")
    return conn

def parse_tz_offset_to_delta(tz_str: str) -> timedelta:
    """
    Convierte '-06:00' -> timedelta(hours=-6), '+05:30' -> +5:30, etc.
    """
    s = tz_str.strip()
    sign = -1 if s.startswith("-") else 1
    hh, mm = s.lstrip("+-").split(":")
    return sign * timedelta(hours=int(hh), minutes=int(mm))

# -----------------------
# Cálculo de ventanas
# -----------------------

def stage_window_bounds_relative() -> tuple[datetime, datetime]:
    """
    Ventana relativa: TARGET_HOURS ± WINDOW_TOLERANCE_H (ambos en horas).
    Retorna (start_utc, end_utc).
    """
    now = _now_utc()
    start = now - timedelta(hours=(TARGET_HOURS + WINDOW_TOLERANCE_H))
    end = now - timedelta(hours=(TARGET_HOURS - WINDOW_TOLERANCE_H))
    return start, end

def day_bounds_utc_for_target_fixed_8am(target_hours: int, tz_offset_str: str = "-06:00") -> tuple[datetime, datetime]:
    """
    Ventana “8 am fijo” por DÍA LOCAL:
      24h -> AYER (local)
      48h -> ANTER (local)
      72h -> HACE 3 DÍAS (local)
    Devuelve (start_utc, end_utc).
    """
    days_back = max(1, target_hours // 24)  # 24=>1, 48=>2, 72=>3
    offset = parse_tz_offset_to_delta(tz_offset_str)

    # ahora en HORA LOCAL
    now_local = _now_utc() + offset
    target_local_date = now_local.date() - timedelta(days=days_back)

    # límites del DÍA LOCAL
    start_local = datetime.combine(target_local_date, datetime.min.time())
    end_local   = datetime.combine(target_local_date, datetime.max.time())

    # convertir a UTC restando el offset
    start_utc = (start_local - offset).replace(tzinfo=timezone.utc)
    end_utc   = (end_local   - offset).replace(tzinfo=timezone.utc)
    return start_utc, end_utc

def compute_window() -> tuple[datetime, datetime, str]:
    if FIXED_8AM_MODE:
        s, e = day_bounds_utc_for_target_fixed_8am(TARGET_HOURS, LOCAL_TZ_OFFSET)
        return s, e, "fixed_8am"
    else:
        s, e = stage_window_bounds_relative()
        return s, e, "relative"

# -----------------------
# Helpers de plantilla / URLs
# -----------------------

def _base_url() -> str:
    """
    Devuelve WISHLIST_URL validada y normalizada, preservando esquema, host y path.
    Ej.: http://localhost/curren  ó  https://www.relojescurrenmexico.com.mx
    """
    url = (WISHLIST_URL or "").rstrip("/")
    parsed = urlparse(url)
    if not (parsed.scheme and parsed.netloc):
        raise RuntimeError(
            "WISHLIST_URL inválida en .env. Ejemplos:\n"
            "  WISHLIST_URL=http://localhost/curren\n"
            "  WISHLIST_URL=https://www.relojescurrenmexico.com.mx"
        )
    return url

def load_template(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def render_products_html(conn, wishlist_id: int) -> str:
    """
    Construye un grid tipo *cards* en 2 columnas (email-safe).
    Muestra hasta 6 ítems, cada uno con imagen, título y botón "Ver producto".
    """
    sql = f"""
        SELECT it.product_id
        FROM {TABLE_PREFIX}tinvwl_items it
        WHERE it.wishlist_id = %s
        ORDER BY it.ID DESC
        LIMIT 6
    """
    products = []
    with conn.cursor(dictionary=True) as cur:
        cur.execute(sql, (wishlist_id,))
        products = cur.fetchall() or []

    if not products:
        return ""

    cards = []
    with conn.cursor(dictionary=True) as cur:
        for row in products:
            pid = row["product_id"]

            # URL directa al producto (fuerte para email)
            product_url = f"{_base_url()}/?post_type=product&p={pid}"

            # thumbnail
            thumb_id = None
            cur.execute(f"""
                SELECT meta_value FROM {TABLE_PREFIX}postmeta
                WHERE post_id=%s AND meta_key='_thumbnail_id' LIMIT 1
            """, (pid,))
            m = cur.fetchone()
            if m and m.get("meta_value"):
                thumb_id = int(m["meta_value"])

            img_url = PLACEHOLDER_IMG
            if thumb_id:
                cur.execute(f"SELECT guid FROM {TABLE_PREFIX}posts WHERE ID=%s LIMIT 1", (thumb_id,))
                img = cur.fetchone()
                if img and img.get("guid"):
                    img_url = img["guid"]

            # título
            cur.execute(f"SELECT post_title FROM {TABLE_PREFIX}posts WHERE ID=%s LIMIT 1", (pid,))
            t = cur.fetchone()
            title = (t.get("post_title") if t else f"Producto {pid}") or f"Producto {pid}"

            card_html = f"""
                <td align="left" valign="top" width="50%" style="width:50%; padding:12px;">
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                         style="border:1px solid #e5e7eb; border-radius:8px; overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,0.06);">
                    <tr>
                      <td align="center" style="padding:16px 16px 8px 16px;">
                        <a href="{product_url}" target="_blank" style="text-decoration:none;">
                          <img src="{img_url}" alt="{title}" width="240"
                               style="display:block; width:100%; max-width:240px; height:auto; border:0;"/>
                        </a>
                      </td>
                    </tr>
                    <tr>
                      <td style="padding:0 16px 12px 16px; color:#111; font-size:14px; line-height:1.35;">
                        {title}
                      </td>
                    </tr>
                    <tr>
                      <td style="padding:0 16px 16px 16px;">
                        <a href="{product_url}" target="_blank"
                           style="display:inline-block; padding:8px 12px; border-radius:6px; background:#111827; color:#ffffff; font-size:13px; text-decoration:none;">
                           Ver producto
                        </a>
                      </td>
                    </tr>
                  </table>
                </td>
                """
            cards.append(card_html)

    # Render en filas de 2 columnas
    rows = []
    for i in range(0, len(cards), 2):
        left = cards[i]
        right = cards[i+1] if i+1 < len(cards) else '<td width="50%" style="width:50%; padding:12px;"></td>'
        rows.append(f"<tr>{left}{right}</tr>")

    return f"""
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
          {''.join(rows)}
        </table>
        """.strip()

def wishlist_link(wishlist_id: int) -> str:
    base = _base_url()
    return f"{base}/lista-de-deseos/?wl={wishlist_id}"

def render_template(conn, wishlist_id: int, base_html: str) -> str:
    products_html = render_products_html(conn, wishlist_id)
    # Valores a inyectar
    mapping = {
        "PRODUCTS": products_html,
        "WISHLIST_LINK": wishlist_link(wishlist_id),
        "YEAR": str(datetime.now().year),
        "LOGO_URL": os.getenv(
            "LOGO_URL",
            f"{_base_url()}/wp-content/uploads/2024/01/logo-curren.png"
        ),
    }

    html = base_html
    for k, v in mapping.items():
        # Soporta {{KEY}} y ${KEY}
        html = html.replace(f"{{{{{k}}}}}", v)
        html = html.replace(f"${{{k}}}", v)
    return html

# -----------------------
# SMTP / envío
# -----------------------

def send_email(to_email: str, subject: str, html: str) -> str:
    """
    Envía correo usando SMTP. Devuelve el Message-ID asignado.
    Mejora de entregabilidad:
      - multipart/alternative (texto plano + HTML)
      - Subject codificado en UTF-8
      - Message-ID propio del dominio y Reply-To
      - (Opcional) List-Unsubscribe si hay URL
    """
    # 1) Construcción del mensaje multipart
    alt = MIMEMultipart("alternative")
    alt["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    alt["To"]   = to_email
    alt["Subject"] = str(Header(subject, "utf-8"))
    alt["Message-ID"] = make_msgid(domain=FROM_EMAIL.split("@")[-1])
    alt["Reply-To"]   = formataddr((FROM_NAME, FROM_EMAIL))

    # List-Unsubscribe (opcional pero recomendado)
    try:
        base = _base_url()
        alt["List-Unsubscribe"] = f"<mailto:{FROM_EMAIL}>, <{base}?unsubscribe=1>"
    except Exception:
        pass

    # 2) Partes: texto plano + HTML (mejora filtros)
    plain = (
        "Hola,\n\n"
        "Dejaste productos en tu wishlist. "
        "Te compartimos el enlace para retomarla.\n\n"
        "Gracias."
    )
    alt.attach(MIMEText(plain, "plain", "utf-8"))
    alt.attach(MIMEText(html,  "html",  "utf-8"))

    print(f"[SMTP] host={SMTP_HOST} port={SMTP_PORT} ssl={SMTP_USE_SSL} user={SMTP_USER}")
    print(f"[SMTP] msgid={alt['Message-ID']} to={to_email}")

    # 3) Conexión y envío
    if SMTP_USE_SSL or SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=25)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=25)
        server.ehlo()
        try:
            server.starttls()
        except Exception:
            pass

    if SMTP_USER:
        server.login(SMTP_USER, SMTP_PASS)

    rejected = server.sendmail(FROM_EMAIL, [to_email], alt.as_string())
    print(f"[SMTP] rejected={rejected}")  # {} == aceptado por el servidor
    server.quit()

    return alt["Message-ID"]

# -----------------------
# Selección de candidatos y log
# -----------------------

def select_candidates(conn, start_utc: datetime, end_utc: datetime) -> list[dict]:
    """
    Selecciona candidatos dentro de la ventana [start_utc, end_utc] (UTC),
    corrigiendo created_at con el offset local almacenado (-06:00 -> +00:00).
    Bloqueo PERMANENTE por campaign_key:
      - Si ya existe en wp_wishlist_email_log una fila con la MISMA campaign_key
        para ese email+wishlist, NO se envía de nuevo (independiente de fecha).
    """
    sql = f"""
        SELECT DISTINCT e.email, wl.ID AS wishlist_id
        FROM {TABLE_PREFIX}wishlist_guest_emails e
        JOIN {TABLE_PREFIX}tinvwl_lists wl  ON wl.ID = e.wishlist_id
        JOIN {TABLE_PREFIX}tinvwl_items it  ON it.wishlist_id = wl.ID
        LEFT JOIN {TABLE_PREFIX}wishlist_email_log log_same
          ON  log_same.email        = e.email
          AND log_same.wishlist_id  = wl.ID
          AND log_same.campaign_key = %s
        WHERE CONVERT_TZ(e.created_at, %s, '+00:00') BETWEEN %s AND %s
          AND log_same.wishlist_id IS NULL   -- <- bloqueo permanente por campaign_key
        LIMIT %s
    """
    with conn.cursor(dictionary=True) as cur:
        cur.execute(
            sql,
            (
                CAMPAIGN_KEY,
                LOCAL_TZ_OFFSET,  # asumiendo created_at guardado en hora local
                start_utc.strftime("%Y-%m-%d %H:%M:%S"),
                end_utc.strftime("%Y-%m-%d %H:%M:%S"),
                MAX_BATCH,
            ),
        )
        rows = cur.fetchall() or []
    return rows

def insert_log(conn, email: str, wishlist_id: int, campaign_key: str):
    sql = f"""
        INSERT INTO {TABLE_PREFIX}wishlist_email_log
          (email, wishlist_id, campaign_key, sent_at)
        VALUES (%s, %s, %s, UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE
          campaign_key = VALUES(campaign_key),
          sent_at      = VALUES(sent_at)
    """
    with conn.cursor(dictionary=True) as cur:
        cur.execute(sql, (email.lower().strip(), wishlist_id, campaign_key))

# -----------------------
# Main
# -----------------------

def main():
    # Ventana de trabajo
    start_utc, end_utc, mode = compute_window()
    # print(f"[DEBUG] Ventana UTC ({mode}): {start_utc:%Y-%m-%d %H:%M:%S}  ->  {end_utc:%Y-%m-%d %H:%M:%S}")

    # Carga plantilla
    try:
        base_template = load_template(TEMPLATE_FILE)
    except Exception as e:
        print(logsafe(f"[ERROR] No se pudo cargar la plantilla {TEMPLATE_FILE}: {e}"))
        sys.exit(2)

    # Conexión DB
    try:
        conn = mysql_conn()
        # DEBUG arranque (puedes quitar cuando todo quede estable)
        print("[BOOT] cwd:", os.getcwd())
        print("[BOOT] script:", __file__)
        print("[BOOT] DB_USER:", DB_USER)
        print("[BOOT] DB_NAME:", DB_NAME)
        print("[BOOT] DB_SOCKET:", DB_SOCKET)
        print("[BOOT] SEND_EMAILS:", os.getenv("SEND_EMAILS"))
        print("[BOOT] STAGE/TARGET/WINDOW:", STAGE, TARGET_HOURS, WINDOW_TOLERANCE_H)
    except Exception as e:
        print(logsafe(f"[ERROR] Conectando MySQL: {e}"))
        sys.exit(3)

    try:
        # Selección
        rows = select_candidates(conn, start_utc, end_utc)
        print(f"[DEBUG] candidatos encontrados: {len(rows)}")

        if not rows:
            print("No hay destinatarios en la ventana de esta etapa.")
            conn.close()
            return

        # Envío
        sent = 0
        for r in rows:
            email = r["email"].strip().lower()
            wishlist_id = int(r["wishlist_id"])

            # Render de HTML
            html = render_template(conn, wishlist_id, base_template)

            if SEND_EMAILS:
                try:
                    message_id = send_email(email, SUBJECT, html)
                    insert_log(conn, email, wishlist_id, CAMPAIGN_KEY)
                    print(logsafe(f"[ENVIADO] {email} (wishlist {wishlist_id}) msgid={message_id}"))
                    sent += 1
                except Exception as e:
                    print(logsafe(f"[ERROR] Enviando a {email} (wishlist {wishlist_id}): {e}"))
            else:
                # Modo “preview”
                print(logsafe(f"[PREVIEW] {email} (wishlist {wishlist_id}) — no se envía por SEND_EMAILS=false"))

    finally:
        try:
            conn.close()
        except Exception:
            pass

if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        print(logsafe(f"[FATAL] {e}"))
        sys.exit(1)
