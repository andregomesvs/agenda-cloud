"""
Agenda Consolidada - versão cloud
-----------------------------------
Roda no Render (ou qualquer host Python), busca os .ics publicados das suas
contas Outlook em segundo plano, guarda tudo no Firestore (Firebase) e serve
o dashboard só pra quem logar com o e-mail autorizado.
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps

import requests
import icalendar
import recurring_ical_events
import firebase_admin
from firebase_admin import credentials, auth as fb_auth, firestore
from flask import Flask, jsonify, request, send_from_directory, g

VALID_STATUS = {"backlog", "fazendo", "feito"}
VALID_PRIORITY = {"urgente", "moderado", "nao_urgente"}

DEFAULT_CONFIG = {
    "accounts": [
        {"name": "Implanta", "color": "#2F6FED", "ics_url": ""},
        {"name": "GFT", "color": "#FF9900", "ics_url": ""},
    ],
    "refresh_minutes": 5,
    "window_days_past": 90,
    "window_days_future": 270,
}

ALLOWED_EMAIL = (os.environ.get("ALLOWED_EMAIL") or "").strip().lower()

# ---------- FIREBASE / FIRESTORE ----------

def _init_firebase():
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        raise RuntimeError(
            "Variável de ambiente FIREBASE_SERVICE_ACCOUNT_JSON não definida. "
            "Cole o conteúdo do arquivo de credencial de serviço do Firebase (JSON) nela."
        )
    cred_dict = json.loads(sa_json)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)


_init_firebase()
db = firestore.client()

CONFIG_REF = db.collection("agenda").document("config")
EVENTS_REF = db.collection("agenda").document("events_cache")
TASKS_COL = db.collection("tasks")

app = Flask(__name__, static_folder="static", static_url_path="")

_events_lock = threading.Lock()
_events_cache = {"events": [], "last_updated": None, "errors": {}, "next_refresh": None}


# ---------- CONFIG ----------

def load_config():
    snap = CONFIG_REF.get()
    if not snap.exists:
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    cfg = snap.to_dict() or {}
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg):
    CONFIG_REF.set(cfg)


# ---------- CALENDÁRIO ----------

def _to_iso(d):
    if isinstance(d, datetime):
        return d.isoformat()
    return datetime(d.year, d.month, d.day).isoformat()


def fetch_account_events(acct, window_start, window_end):
    url = (acct.get("ics_url") or "").strip()
    if not url:
        return [], None
    try:
        resp = requests.get(
            url, timeout=25, headers={"User-Agent": "AgendaConsolidada/1.0"}
        )
        resp.raise_for_status()
        cal = icalendar.Calendar.from_ical(resp.content)
        occurrences = recurring_ical_events.of(cal).between(window_start, window_end)

        events = []
        for evt in occurrences:
            summary = str(evt.get("SUMMARY", "") or "(sem título)")
            location = str(evt.get("LOCATION", "") or "")
            dtstart = evt.get("DTSTART").dt
            dtend_field = evt.get("DTEND")
            dtend = dtend_field.dt if dtend_field else dtstart
            all_day = not isinstance(dtstart, datetime)
            events.append(
                {
                    "summary": summary,
                    "location": location,
                    "start": _to_iso(dtstart),
                    "end": _to_iso(dtend),
                    "all_day": all_day,
                    "account": acct.get("name", "Conta"),
                    "color": acct.get("color", "#888888"),
                }
            )
        return events, None
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"


def do_refresh():
    cfg = load_config()
    now = datetime.now()
    window_start = now - timedelta(days=cfg.get("window_days_past", 90))
    window_end = now + timedelta(days=cfg.get("window_days_future", 270))

    all_events = []
    errors = {}
    for acct in cfg["accounts"]:
        events, err = fetch_account_events(acct, window_start, window_end)
        all_events.extend(events)
        if err:
            errors[acct.get("name", "Conta")] = err

    all_events.sort(key=lambda e: e["start"])
    next_refresh = (now + timedelta(minutes=cfg.get("refresh_minutes", 5))).isoformat()

    payload = {
        "events": all_events,
        "last_updated": now.isoformat(),
        "errors": errors,
        "next_refresh": next_refresh,
    }

    with _events_lock:
        _events_cache.update(payload)

    # Firestore tem limite de 1MB por documento; agendas muito grandes cabem
    # tranquilamente (cada evento é só texto curto). Guardamos lá pra
    # sobreviver a reinícios do servidor no Render (disco não é persistente).
    try:
        EVENTS_REF.set(payload)
    except Exception as e:  # noqa: BLE001
        print("Aviso: não consegui salvar o cache de eventos no Firestore:", e)

    print(
        f"[{now.strftime('%H:%M:%S')}] atualizado: {len(all_events)} eventos"
        + (f" | erros: {errors}" if errors else "")
    )


def refresh_loop():
    while True:
        try:
            do_refresh()
        except Exception as e:  # noqa: BLE001
            print("Erro no ciclo de atualização:", e)
        cfg = load_config()
        minutes = max(1, cfg.get("refresh_minutes", 5))
        time.sleep(minutes * 60)


# ---------- TAREFAS (Firestore) ----------

def load_tasks():
    docs = TASKS_COL.stream()
    tasks = [d.to_dict() for d in docs]
    tasks.sort(key=lambda t: t.get("created_at", ""))
    return tasks


def create_task(data):
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("título é obrigatório")
    now_iso = datetime.now().isoformat()
    task = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": (data.get("description") or "").strip(),
        "company": (data.get("company") or "").strip(),
        "start_date": data.get("start_date") or "",
        "end_date": data.get("end_date") or "",
        "priority": data.get("priority") if data.get("priority") in VALID_PRIORITY else "moderado",
        "status": data.get("status") if data.get("status") in VALID_STATUS else "backlog",
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    TASKS_COL.document(task["id"]).set(task)
    return task


def update_task(task_id, data):
    ref = TASKS_COL.document(task_id)
    snap = ref.get()
    if not snap.exists:
        return None

    updates = {}
    if "title" in data:
        title = (data.get("title") or "").strip()
        if not title:
            raise ValueError("título é obrigatório")
        updates["title"] = title
    if "description" in data:
        updates["description"] = (data.get("description") or "").strip()
    if "company" in data:
        updates["company"] = (data.get("company") or "").strip()
    if "start_date" in data:
        updates["start_date"] = data.get("start_date") or ""
    if "end_date" in data:
        updates["end_date"] = data.get("end_date") or ""
    if "priority" in data and data["priority"] in VALID_PRIORITY:
        updates["priority"] = data["priority"]
    if "status" in data and data["status"] in VALID_STATUS:
        updates["status"] = data["status"]
    updates["updated_at"] = datetime.now().isoformat()

    ref.update(updates)
    return ref.get().to_dict()


def delete_task(task_id):
    ref = TASKS_COL.document(task_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True


# ---------- AUTENTICAÇÃO ----------

def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        authz = request.headers.get("Authorization", "")
        if not authz.startswith("Bearer "):
            return jsonify({"ok": False, "error": "não autenticado"}), 401
        id_token = authz.split(" ", 1)[1]
        try:
            decoded = fb_auth.verify_id_token(id_token)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"token inválido: {e}"}), 401

        email = (decoded.get("email") or "").strip().lower()
        if not decoded.get("email_verified") or not ALLOWED_EMAIL or email != ALLOWED_EMAIL:
            return jsonify({"ok": False, "error": "este e-mail não tem acesso a este app"}), 403

        g.user_email = email
        return f(*args, **kwargs)

    return wrapper


# ---------- ROTAS PÚBLICAS ----------

@app.route("/healthz")
def healthz():
    # sem autenticação: serve pra um pinger externo (ex. UptimeRobot) manter
    # o servidor acordado no plano gratuito do Render
    return jsonify({"ok": True})


@app.route("/api/firebase-config")
def api_firebase_config():
    # Estes valores do Firebase Web SDK não são segredo — são feitos pra
    # ficar no código do navegador. A segurança de verdade é o
    # verify_id_token() + checagem de e-mail no backend.
    return jsonify(
        {
            "apiKey": os.environ.get("FIREBASE_WEB_API_KEY", ""),
            "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
            "projectId": os.environ.get("FIREBASE_PROJECT_ID", ""),
            "appId": os.environ.get("FIREBASE_APP_ID", ""),
        }
    )


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------- ROTAS PROTEGIDAS ----------

@app.route("/api/events")
@require_auth
def api_events():
    with _events_lock:
        return jsonify(_events_cache)


@app.route("/api/config", methods=["GET"])
@require_auth
def api_get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
@require_auth
def api_set_config():
    cfg = request.get_json(force=True) or {}
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    save_config(cfg)
    threading.Thread(target=do_refresh, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/refresh", methods=["POST"])
@require_auth
def api_refresh():
    threading.Thread(target=do_refresh, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/tasks", methods=["GET"])
@require_auth
def api_get_tasks():
    return jsonify(load_tasks())


@app.route("/api/tasks", methods=["POST"])
@require_auth
def api_create_task():
    data = request.get_json(force=True) or {}
    try:
        task = create_task(data)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "task": task})


@app.route("/api/tasks/<task_id>", methods=["PUT"])
@require_auth
def api_update_task(task_id):
    data = request.get_json(force=True) or {}
    try:
        task = update_task(task_id, data)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if task is None:
        return jsonify({"ok": False, "error": "tarefa não encontrada"}), 404
    return jsonify({"ok": True, "task": task})


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
@require_auth
def api_delete_task(task_id):
    ok = delete_task(task_id)
    if not ok:
        return jsonify({"ok": False, "error": "tarefa não encontrada"}), 404
    return jsonify({"ok": True})


# ---------- START ----------

if __name__ == "__main__":
    if not ALLOWED_EMAIL:
        print("AVISO: variável ALLOWED_EMAIL não definida — ninguém vai conseguir logar.")
    load_config()
    do_refresh()
    threading.Thread(target=refresh_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"Agenda Consolidada (cloud) rodando na porta {port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
