#!/usr/bin/env python3
import os
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

WORKER_FILE = "wishlist_reminder.py"   # worker genérico

# Lock-file en carpeta temporal del SO (Windows friendly)
LOCK_FILE = str(Path(os.getenv("TEMP", ".")) / "wishlist_orch.lock")

LOG_DIR = Path(__file__).with_name("logs")
LOG_DIR.mkdir(exist_ok=True)

MAX_RETRIES = int(os.getenv("ORCH_MAX_RETRIES", "2"))
BACKOFF_SECS = [10, 30]

def _safe_console_write(s: str) -> None:
    """Escribe siempre algo, aunque la consola no soporte UTF-8 (Windows)."""
    try:
        print(s, flush=True)
    except Exception:
        try:
            sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))
            sys.stdout.flush()
        except Exception:
            sys.stdout.write(s.encode("ascii", "ignore").decode("ascii") + "\n")
            sys.stdout.flush()

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    _safe_console_write(line)
    with (LOG_DIR / f"orchestrator_{datetime.now():%Y%m%d}.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def acquire_lock() -> bool:
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        return True
    except FileExistsError:
        return False

def release_lock():
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass

def _redact_env_for_log(env_overrides: dict) -> dict:
    """Evita imprimir SUBJECT (con emojis) y credenciales."""
    redact_keys = {"SUBJECT", "SMTP_PASS", "DB_PASS"}
    out = {}
    for k, v in env_overrides.items():
        out[str(k)] = "***redacted***" if k in redact_keys else str(v)
    return out

def run_worker(env_overrides: dict) -> int:
    # ⚠️ Windows: el environment de CreateProcess debe ser str->str
    env = {str(k): str(v) for k, v in os.environ.items()}
    env.update({str(k): str(v) for k, v in env_overrides.items()})

    python_bin = sys.executable
    worker_path = Path(__file__).with_name(WORKER_FILE)
    if not worker_path.exists():
        log(f"[ERROR] No encontré el worker en {worker_path}")
        return 127

    log(f"Iniciando worker: {worker_path.name} con env overrides={_redact_env_for_log(env_overrides)}")
    proc = subprocess.Popen(
        [python_bin, str(worker_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        try:
            _safe_console_write(line.decode("utf-8", "replace").rstrip("\n"))
        except Exception:
            pass
    proc.wait()
    log(f"Worker terminó con código {proc.returncode}")
    return proc.returncode

def main():
    load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=False)

    # ===== Modo prueba local =====
    LOCAL_TEST_MODE = os.getenv("LOCAL_TEST_MODE", "false").strip().lower() == "true"
    TEST_DELAY_MIN = os.getenv("TEST_DELAY_MIN")  # si se define, reemplaza cualquier delay por-etapa

    if LOCAL_TEST_MODE:
        log("[MODO PRUEBA LOCAL] Activado — se aplicarán pausas entre etapas.")
        if TEST_DELAY_MIN:
            log(f"[MODO PRUEBA LOCAL] TEST_DELAY_MIN={TEST_DELAY_MIN} minuto(s) (override global).")
    else:
        log("[MODO PRODUCCIÓN] Sin pausas artificiales entre etapas.")

    stages = [
        {
            "STAGE": "24",
            "TARGET_HOURS": "24",
            "WINDOW_TOLERANCE_H": "6",
            "CAMPAIGN_KEY": "wishlist_v1_24h",
            "TEMPLATE_FILE": "templates/wishlist_email_24h.html",
            "SUBJECT": "Tu reloj favorito te espera ⌚",
            "DELAY_AFTER_MIN": 2,   # solo se usa si LOCAL_TEST_MODE=true
        },
        {
            "STAGE": "48",
            "TARGET_HOURS": "48",
            "WINDOW_TOLERANCE_H": "6",
            "CAMPAIGN_KEY": "wishlist_v1_48h",
            "TEMPLATE_FILE": "templates/wishlist_email_48h.html",
            "SUBJECT": "Aún estás a tiempo — 10% OFF termina pronto",
            "DELAY_AFTER_MIN": 2,
        },
        {
            "STAGE": "72",
            "TARGET_HOURS": "72",
            "WINDOW_TOLERANCE_H": "6",
            "CAMPAIGN_KEY": "wishlist_v1_72h",
            "TEMPLATE_FILE": "templates/wishlist_email_72h.html",
            "SUBJECT": "Última oportunidad ⏰ se están agotando",
            "DELAY_AFTER_MIN": 0,
        },
    ]

    common_overrides = {
        "MAX_BATCH": os.getenv("MAX_BATCH", "300"),
        "SEND_EMAILS": os.getenv("SEND_EMAILS", "false"),
    }

    if not acquire_lock():
        log("Otro proceso está corriendo (lock presente). Salgo.")
        return 0

    try:
        for st in stages:
            overrides = {**common_overrides, **st}
            attempt = 0
            while True:
                rc = run_worker(overrides)
                if rc == 0:
                    break
                attempt += 1
                if attempt > MAX_RETRIES:
                    log(f"[FATAL] Worker falló tras {MAX_RETRIES} reintentos. rc={rc} stage={st['STAGE']}")
                    return rc
                sleep_for = BACKOFF_SECS[min(attempt-1, len(BACKOFF_SECS)-1)]
                log(f"[WARN] Stage {st['STAGE']} falló intento {attempt}/{MAX_RETRIES}. Reintentando en {sleep_for}s…")
                time.sleep(sleep_for)

            # Pausa entre etapas solo en modo prueba local
            if LOCAL_TEST_MODE:
                delay_min = int(TEST_DELAY_MIN) if TEST_DELAY_MIN else int(st.get("DELAY_AFTER_MIN", 0))
                if delay_min > 0:
                    total_sec = delay_min * 60
                    log(f"Esperando {delay_min} minuto(s) antes de la siguiente etapa…")
                    for s in range(total_sec, 0, -1):
                        time.sleep(1)
                        if s % 10 == 0 or s <= 5:
                            log(f"…faltan {s} s")
        return 0
    finally:
        release_lock()

if __name__ == "__main__":
    sys.exit(main())
