import os
import pymysql
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
from dotenv import load_dotenv

# --------------------------------------
# CONFIG
# --------------------------------------
load_dotenv()  # lee variables desde .env si lo usas

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "usuario_mysql")
DB_PASS = os.getenv("DB_PASS", "password_mysql")
DB_NAME = os.getenv("DB_NAME", "relojesc_relob")
DB_PORT = int(os.getenv("DB_PORT", "3306"))

# SMTP (si vas a enviar)
SEND_EMAILS = False  # <- cambia a True cuando quieras enviar de verdad
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.tu-proveedor.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "tu@dominio.com")
SMTP_PASS = os.getenv("SMTP_PASS", "tu_password")
FROM_NAME = os.getenv("FROM_NAME", "Curren México")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)

# URL base para que el usuario vea su wishlist (ajústala a tu página de lista de deseos)
WISHLIST_URL = os.getenv("WISHLIST_URL", "https://tusitio.com/lista-de-deseos/")

# --------------------------------------
# SQL (la que confirmaste que funciona)
# --------------------------------------
SQL_WISHLIST_INVITADOS = """
SELECT 
  e.email,
  e.created_at,
  wl.ID AS wishlist_id,
  GROUP_CONCAT(DISTINCT it.product_id) AS product_ids
FROM wp_wishlist_guest_emails e
JOIN wp_tinvwl_lists wl 
  ON wl.ID = e.wishlist_id
JOIN wp_tinvwl_items it 
  ON it.wishlist_id = wl.ID
GROUP BY e.email, wl.ID, e.created_at
ORDER BY e.created_at DESC;
"""

# (Opcional) Si quieres también los títulos en el mismo query, usa este en vez del de arriba.
# SQL_WISHLIST_INVITADOS = """
# SELECT 
#   e.email,
#   e.created_at,
#   wl.ID AS wishlist_id,
#   GROUP_CONCAT(DISTINCT it.product_id) AS product_ids,
#   GROUP_CONCAT(DISTINCT p.post_title ORDER BY p.post_title SEPARATOR ', ') AS product_titles
# FROM wp_wishlist_guest_emails e
# JOIN wp_tinvwl_lists wl  ON wl.ID = e.wishlist_id
# JOIN wp_tinvwl_items it  ON it.wishlist_id = wl.ID
# JOIN wp_posts p          ON p.ID = it.product_id AND p.post_type='product' AND p.post_status='publish'
# GROUP BY e.email, wl.ID, e.created_at
# ORDER BY e.created_at DESC;
# """

# --------------------------------------
# Helpers
# --------------------------------------
def get_db_conn():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        port=DB_PORT,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )

def build_email_subject():
    return "Tu reloj favorito aún te está esperando ⌚"

def build_email_body(email, wishlist_id, product_ids, product_titles=None):
    # URL donde el usuario puede revisar su lista (puedes anexar parámetros si te interesan)
    wishlist_link = f"{WISHLIST_URL}?wl={wishlist_id}"

    # Texto base
    line_items = ""
    if product_titles:
        line_items = f"\nProductos guardados: {product_titles}\n"

    body = f"""Hola,

Notamos que guardaste algunos relojes en tu lista de deseos.
{line_items}
Buenas noticias: ¡todavía están disponibles!

Haz tu pedido hoy y luce tu estilo con 10% OFF en tu primera compra.
(No necesitas aplicar nada: el descuento se aplica automáticamente).

👉 Ver mi lista de deseos: {wishlist_link}

Si necesitas ayuda, responde a este email y con gusto te atendemos.
— Curren México
"""
    return body

def send_email(to_email, subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, [to_email], msg.as_string())

# --------------------------------------
# Main
# --------------------------------------
def main():
    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute(SQL_WISHLIST_INVITADOS)
        rows = cur.fetchall()

    if not rows:
        print("No hay registros de wishlist de invitados con email guardado.")
        return

    print(f"Se encontraron {len(rows)} destinatarios.")
    for r in rows:
        email = r["email"]
        wishlist_id = r["wishlist_id"]
        product_ids = (r.get("product_ids") or "").split(",") if r.get("product_ids") else []
        product_titles = r.get("product_titles")  # sólo si usas el query opcional

        subject = build_email_subject()
        body = build_email_body(email, wishlist_id, product_ids, product_titles)

        if SEND_EMAILS:
            try:
                send_email(email, subject, body)
                print(f"[ENVIADO] {email} (wishlist {wishlist_id})")
            except Exception as e:
                print(f"[ERROR] {email}: {e}")
        else:
            # Modo prueba (no envía)
            print("----- PREVIEW -----")
            print(f"Para: {email}")
            print(f"Asunto: {subject}")
            print(body)
            print("-------------------\n")

if __name__ == "__main__":
    main()