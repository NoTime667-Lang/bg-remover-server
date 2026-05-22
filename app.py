import os
import uuid
import json
import hashlib
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, jsonify, request, render_template, send_file, redirect, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INSTALLS_FILE = os.path.join(DATA_DIR, "installs.json")
VERSIONS_FILE = os.path.join(DATA_DIR, "versions.json")
# Updates hosted locally (uploaded via admin API)
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


def _load_installs():
    if not os.path.exists(INSTALLS_FILE):
        return []
    with open(INSTALLS_FILE) as f:
        return json.load(f)


def _save_installs(data):
    with open(INSTALLS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_versions():
    if not os.path.exists(VERSIONS_FILE):
        return {"latest": "1.0.0", "versions": {}}
    with open(VERSIONS_FILE) as f:
        return json.load(f)


def _save_versions(data):
    with open(VERSIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


# ── Public pages ──

@app.route("/")
def index():
    versions = _load_versions()
    return render_template("index.html", latest_version=versions["latest"],
                           download_url=f"/api/download/{versions['latest']}")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return render_template("login.html", error="Mot de passe incorrect")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.pop("admin", None)
    return redirect("/")


# ── Admin pages ──

@app.route("/admin")
@login_required
def admin():
    installs = _load_installs()
    total = len(installs)
    last_24h = 0
    version_counts = {}
    for i in installs:
        v = i.get("version", "unknown")
        version_counts[v] = version_counts.get(v, 0) + 1
        try:
            seen = datetime.fromisoformat(i["last_seen"])
            if (datetime.now(timezone.utc) - seen).total_seconds() < 86400:
                last_24h += 1
        except Exception:
            pass
    versions = _load_versions()
    return render_template("admin.html", total=total, last_24h=last_24h,
                           version_counts=version_counts,
                           latest_version=versions["latest"],
                           versions=versions["versions"],
                           installs=sorted(installs, key=lambda x: x["last_seen"], reverse=True))


# ── API (public, used by the app) ──

@app.route("/api/ping", methods=["POST"])
def ping():
    data = request.get_json(silent=True) or {}
    uid = data.get("id") or str(uuid.uuid4())
    version = data.get("version", "1.0.0")

    installs = _load_installs()
    existing = [i for i in installs if i["id"] == uid]
    now = datetime.now(timezone.utc).isoformat()

    if existing:
        existing[0]["last_seen"] = now
        existing[0]["version"] = version
        existing[0]["count"] = existing[0].get("count", 1) + 1
    else:
        installs.append({
            "id": uid,
            "version": version,
            "first_seen": now,
            "last_seen": now,
            "count": 1,
        })

    _save_installs(installs)
    return jsonify({"ok": True, "id": uid, "total": len(installs)})


@app.route("/api/check/<current_version>")
def check_update(current_version):
    versions = _load_versions()
    latest = versions.get("latest", "1.0.0")

    def parse(v):
        try:
            return tuple(int(x) for x in v.split("."))
        except Exception:
            return (0,)

    update_available = parse(latest) > parse(current_version)
    notes = versions.get("versions", {}).get(latest, {}).get("notes", "")
    return jsonify({
        "update_available": update_available,
        "latest_version": latest,
        "download_url": f"/api/download/{latest}" if update_available else None,
        "release_notes": notes,
    })


@app.route("/api/download/<version>")
def download(version):
    versions = _load_versions()
    if version not in versions.get("versions", {}):
        return jsonify({"error": "Version not found"}), 404
    info = versions["versions"][version]
    fname = info.get("filename", f"BG Remover {version}.zip")
    fpath = os.path.join(UPLOAD_DIR, fname)
    if not os.path.exists(fpath):
        # Fallback to release_url if configured
        if info.get("release_url"):
            return redirect(info["release_url"])
        return jsonify({"error": "File not uploaded yet"}), 404
    return send_file(fpath, as_attachment=True, download_name=fname)


# ── Admin API ──

@app.route("/api/admin/register-version", methods=["POST"])
def register_version():
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {ADMIN_PASSWORD}":
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    version = data.get("version")
    notes = data.get("notes", "")
    if not version:
        return jsonify({"error": "version required"}), 400

    versions = _load_versions()
    versions["latest"] = version
    existing = versions["versions"].get(version, {})
    versions["versions"][version] = {
        "notes": notes,
        "release_url": data.get("release_url", existing.get("release_url", "")),
        "filename": data.get("filename", existing.get("filename", f"BG Remover {version}.zip")),
        "released": datetime.now(timezone.utc).isoformat(),
    }
    _save_versions(versions)
    return jsonify({"ok": True})


@app.route("/api/admin/upload", methods=["POST"])
def upload_file():
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {ADMIN_PASSWORD}":
        return jsonify({"error": "Unauthorized"}), 401

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    version = request.form.get("version", "")
    notes = request.form.get("notes", "")

    if not version:
        return jsonify({"error": "version required"}), 400

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    fname = f"BG Remover {version}.zip"
    fpath = os.path.join(UPLOAD_DIR, fname)
    f.save(fpath)

    # Register version
    versions = _load_versions()
    versions["latest"] = version
    versions["versions"][version] = {
        "notes": notes,
        "release_url": "",
        "filename": fname,
        "released": datetime.now(timezone.utc).isoformat(),
    }
    _save_versions(versions)

    return jsonify({"ok": True, "version": version, "size": os.path.getsize(fpath)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
