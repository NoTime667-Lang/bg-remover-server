import os
import uuid
import json
from datetime import datetime, timezone
from flask import Flask, jsonify, request, render_template, send_file

app = Flask(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INSTALLS_FILE = os.path.join(DATA_DIR, "installs.json")
VERSIONS_FILE = os.path.join(DATA_DIR, "versions.json")
UPDATES_DIR = os.path.join(DATA_DIR, "updates")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPDATES_DIR, exist_ok=True)


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


# ── API ──

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


@app.route("/api/stats")
def stats():
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

    return jsonify({
        "total": total,
        "last_24h": last_24h,
        "versions": version_counts,
    })


@app.route("/api/check/<current_version>")
def check_update(current_version):
    versions = _load_versions()
    latest = versions.get("latest", "1.0.0")

    def parse(v):
        return tuple(int(x) for x in v.split("."))

    update_available = parse(latest) > parse(current_version)
    return jsonify({
        "update_available": update_available,
        "latest_version": latest,
        "download_url": f"/api/download/{latest}" if update_available else None,
        "release_notes": versions.get("versions", {}).get(latest, {}).get("notes", ""),
    })


@app.route("/api/download/<version>")
def download(version):
    versions = _load_versions()
    if version not in versions.get("versions", {}):
        return jsonify({"error": "Version not found"}), 404
    fname = versions["versions"][version].get("filename")
    if not fname:
        return jsonify({"error": "No file for this version"}), 404
    fpath = os.path.join(UPDATES_DIR, fname)
    if not os.path.exists(fpath):
        return jsonify({"error": "File not available"}), 404
    return send_file(fpath, as_attachment=True)


@app.route("/api/admin/register-version", methods=["POST"])
def register_version():
    data = request.get_json(silent=True) or {}
    version = data.get("version")
    notes = data.get("notes", "")
    if not version:
        return jsonify({"error": "version required"}), 400

    versions = _load_versions()
    versions["latest"] = version
    versions["versions"][version] = {
        "notes": notes,
        "filename": data.get("filename", f"BG Remover {version}.exe"),
        "released": datetime.now(timezone.utc).isoformat(),
    }
    _save_versions(versions)
    return jsonify({"ok": True})


# ── Website ──

@app.route("/")
def index():
    installs = _load_installs()
    total = len(installs)
    last_24h = 0
    for i in installs:
        try:
            seen = datetime.fromisoformat(i["last_seen"])
            if (datetime.now(timezone.utc) - seen).total_seconds() < 86400:
                last_24h += 1
        except Exception:
            pass
    versions = _load_versions()
    return render_template("index.html", total=total, last_24h=last_24h,
                           latest_version=versions["latest"],
                           installs=installs)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
