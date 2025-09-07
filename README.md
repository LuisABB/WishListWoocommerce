# 📧 Wishlist Reminder – TI WooCommerce Wishlist (DB version)

Este script en Python 3 se conecta directamente a la base de datos de WordPress/WooCommerce para detectar listas de deseos creadas con el plugin **TI WooCommerce Wishlist**.

Envía un correo recordatorio automático a los usuarios que añadieron un producto a su lista de deseos hace **24–48 horas**, animándolos a completar su compra.

---

## 🚀 Funcionalidad

- Se conecta a MySQL/MariaDB usando `pymysql`.
- Detecta wishlists con productos añadidos hace entre 24 y 48 horas.
- Obtiene el email del usuario (`wp_users.user_email` o datos guardados en el plugin).
- Envía un correo recordatorio en texto plano vía SMTP.
- Registra los envíos en la tabla `wp_wishlist_reminder_log` para evitar duplicados.
- Compatible con usuarios registrados e invitados.

---

## 📦 Requisitos

- Python 3.9+
- Dependencias:

```bash
pip install pymysql
```

---

## ⚙️ Configuración

Edita las variables de entorno o modifica las constantes al inicio del script:

### 🔑 Base de datos (WordPress)
```
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=wp_user
DB_PASS=wp_password
DB_NAME=wordpress
TABLE_PREFIX=wp_
```

### 📧 SMTP
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tu_correo@dominio.com
SMTP_PASS=tu_app_password
FROM_EMAIL=ventas@tudominio.com
FROM_NAME="Curren México"
```

### 🌐 URLs
```
STORE_NAME="Curren México"
CATALOG_URL="https://www.relojescurrenmexico.com.mx/tienda"
WISHLIST_URL="https://www.relojescurrenmexico.com.mx/wishlist/"
WHATSAPP_URL="https://wa.me/5210000000000"
```

---

## ▶️ Ejecución

### Simulación (sin enviar emails)
```bash
python wishlist_reminder_db.py --dry-run
```

### Ejecución real
```bash
python wishlist_reminder_db.py
```

---

## 🔄 Automatización

Programa el script en `cron` o **Task Scheduler** para ejecutarlo cada 1–3 horas.

Ejemplo en cron (ejecutar cada 2 horas):

```
0 */2 * * * /usr/bin/python3 /ruta/wishlist_reminder_db.py >> /var/log/wishlist_reminder.log 2>&1
```

---

## 📊 Lógica del envío

- **Ventana de tiempo**: entre 24h y 48h después de la primera adición a la wishlist.
- **Control**: tabla `wp_wishlist_reminder_log` (se crea automáticamente si no existe).
- **Un envío por wishlist** (no repite).

---

## ✨ Personalización

- Puedes cambiar el texto del correo en la función `build_email()`.
- Puedes unir `product_id` con `wp_posts` para incluir el nombre real del producto y/o su imagen.
- Puedes ajustar la ventana de envío modificando `MIN_DELAY` y `MAX_DELAY`.

---

## ⚠️ Notas importantes

- Asegúrate de que tu política de privacidad permita este tipo de comunicaciones.
- Revisa que tu servidor SMTP (ej. Gmail, SendGrid, Amazon SES) soporte el volumen de correos que planeas enviar.
- Haz pruebas con `--dry-run` antes de habilitar el envío real.
