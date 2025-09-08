# Wishlist Reminder Worker

Este proyecto implementa un **worker en Python** que envía correos automáticos de recordatorio a los usuarios que han guardado productos en su lista de deseos (wishlist).

## 🚀 Características principales
- Envía recordatorios en 3 etapas:
  - **24h**: recordatorio emocional (tono suave).
  - **48h**: incentivo con cupón de descuento.
  - **72h**: último recordatorio con urgencia.
- Compatible con **modo relativo** o **modo fijo 8AM** (recomendado para producción).
- Evita reenvíos mediante tabla de log en MySQL (`wp_wishlist_email_log`).
- Renderiza plantillas HTML con los productos guardados, enlaces y cupón.

## 📂 Estructura
- `wishlist_reminder.py` → worker principal.
- `templates/` → plantillas HTML (`wishlist_email_24h.html`, `wishlist_email_48h.html`, `wishlist_email_72h.html`).
- `.env` → variables de configuración (DB, SMTP, etc.).

## ⚙️ Configuración

1. Crea un archivo `.env` en la raíz del proyecto con las variables necesarias:

```env
# Base de datos
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASS=
DB_NAME=relojesc_relob

# SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tu_correo@gmail.com
SMTP_PASS=tu_password
FROM_NAME="Curren México"
FROM_EMAIL=tu_correo@gmail.com

# URL base de la tienda
WISHLIST_URL=https://www.relojescurrenmexico.com.mx

# Control de campaña
FIXED_8AM_MODE=true
LOCAL_TZ_OFFSET=-06:00
COOLDOWN_HOURS=168
MAX_BATCH=300
SEND_EMAILS=true
```

2. Instala dependencias:

```bash
pip install -r requirements.txt
```

Dependencias principales:
- `pymysql`
- `python-dotenv`

## 🗄️ Base de datos

Tabla de log para evitar reenvíos:

```sql
CREATE TABLE IF NOT EXISTS wp_wishlist_email_log (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL,
  wishlist_id BIGINT UNSIGNED NOT NULL,
  campaign_key VARCHAR(64) NOT NULL,
  sent_at DATETIME NOT NULL,
  UNIQUE KEY uniq_email_wl (email, wishlist_id),
  KEY idx_email (email),
  KEY idx_sent_at (sent_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

👉 Cada fila mantiene el último envío por `email+wishlist_id`.  
👉 `campaign_key` y `sent_at` se actualizan en cada envío.

## ▶️ Ejecución local

Ejemplo en PowerShell para probar cada etapa:

### 24 horas
```powershell
$env:STAGE="24"; `
$env:TARGET_HOURS="24"; `
$env:CAMPAIGN_KEY="wishlist_v1_24h"; `
$env:TEMPLATE_FILE="templates/wishlist_email_24h.html"; `
$env:SUBJECT="Tu reloj favorito te espera ⌚"; `
python .\wishlist_reminder.py
```

```SQL
UPDATE wp_wishlist_guest_emails
SET created_at = CONCAT(DATE_SUB(CURDATE(), INTERVAL 1 DAY), ' 12:00:00')
WHERE email='bettoapellido@gmail.com' AND wishlist_id=4;
```

### 48 horas
```powershell
$env:STAGE="48"; `
$env:TARGET_HOURS="48"; `
$env:CAMPAIGN_KEY="wishlist_v1_48h"; `
$env:TEMPLATE_FILE="templates/wishlist_email_48h.html"; `
$env:SUBJECT="Aún estás a tiempo — 10% OFF termina pronto"; `
python .\wishlist_reminder.py
```

```SQL
UPDATE wp_wishlist_guest_emails
SET created_at = CONCAT(DATE_SUB(CURDATE(), INTERVAL 2 DAY), ' 12:00:00')
WHERE email='bettoapellido@gmail.com' AND wishlist_id=4;
```

### 72 horas
```powershell
$env:STAGE="72"; `
$env:TARGET_HOURS="72"; `
$env:CAMPAIGN_KEY="wishlist_v1_72h"; `
$env:TEMPLATE_FILE="templates/wishlist_email_72h.html"; `
$env:SUBJECT="Última oportunidad ⏰"; `
python .\wishlist_reminder.py
```
```SQL
UPDATE wp_wishlist_guest_emails
SET created_at = CONCAT(DATE_SUB(CURDATE(), INTERVAL 3 DAY), ' 12:00:00')
WHERE email='bettoapellido@gmail.com' AND wishlist_id=4;
```

## 🕒 Producción (CRON)

Ejecutar diariamente a las 8AM (hora CDMX):

```cron
0 8 * * * STAGE=24 TARGET_HOURS=24 CAMPAIGN_KEY=wishlist_v1_24h TEMPLATE_FILE=templates/wishlist_email_24h.html SUBJECT="Tu reloj favorito te espera ⌚" python /ruta/wishlist_reminder.py
0 8 * * * STAGE=48 TARGET_HOURS=48 CAMPAIGN_KEY=wishlist_v1_48h TEMPLATE_FILE=templates/wishlist_email_48h.html SUBJECT="Aún estás a tiempo — 10% OFF termina pronto" python /ruta/wishlist_reminder.py
0 8 * * * STAGE=72 TARGET_HOURS=72 CAMPAIGN_KEY=wishlist_v1_72h TEMPLATE_FILE=templates/wishlist_email_72h.html SUBJECT="Última oportunidad ⏰" python /ruta/wishlist_reminder.py
```

## 📧 Flujo de correos

- **24h** → recordatorio emocional + disponibilidad.  
- **48h** → incentivo de compra con cupón.  
- **72h** → urgencia: última llamada antes de perder el descuento.

---
