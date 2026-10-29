"""OMR 2.5: aplicación local para fichas A4 horizontales y resultados por grado."""
from __future__ import annotations
import io
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from contextlib import contextmanager

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

BASE_DIR = Path(__file__).resolve().parent
COURSES = {"Matematica": "Matemática", "Comunicacion": "Comunicación"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
DEFAULTS = {
    "version": "2.5", "branding": {"title": "CONCURSO FERMAT", "logo": "", "logo_left": "", "logo_right": ""},
    "scoring": {"buenas": 10.0, "malas": -1.0, "blanco": 0.0},
    "num_questions": 15, "num_alternatives": 5,
}
LOCK = threading.RLock()


def _plain(value):
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items() if not isinstance(v, np.ndarray)}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _course(value):
    if value not in COURSES:
        raise ValueError("Seleccione Matemática o Comunicación.")
    return value


def _grade(value):
    if isinstance(value, bool) or str(value) not in {str(i) for i in range(1, 7)}:
        raise ValueError("El grado debe ser un número del 1 al 6.")
    return int(value)


def _answers(value, *, key=False):
    if isinstance(value, str):
        value = list(re.sub(r"[\s,;]+", "", value.upper()))
    if not isinstance(value, list) or len(value) != 15:
        raise ValueError("Se requieren exactamente 15 respuestas.")
    allowed = set("ABCDE") if key else set("ABCDE-X")
    value = [str(x).strip().upper() for x in value]
    if any(v not in allowed for v in value):
        raise ValueError("La clave admite A–E. Para revisar respuestas, use A–E, - (blanco) o X (doble).")
    return value


def create_app(data_dir=None, testing=False):
    app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
    root = Path(data_dir or os.environ.get("OMR25_DATA_DIR") or BASE_DIR / "data").resolve()
    for folder in (root, root / "uploads", root / "processed", root / "branding"):
        folder.mkdir(parents=True, exist_ok=True)
    app.config.update(TESTING=testing, MAX_CONTENT_LENGTH=200 * 1024 * 1024, DATA_DIR=root)
    database = root / "omr25.sqlite3"
    settings_file = root / "settings.json"

    @contextmanager
    def connect():
        conn = sqlite3.connect(database, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, course TEXT NOT NULL, grade INTEGER,
          confirmed INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS answer_keys(course TEXT NOT NULL, grade INTEGER NOT NULL,
          answers TEXT NOT NULL, PRIMARY KEY(course,grade));
        """)

    def settings():
        result = json.loads(json.dumps(DEFAULTS))
        if settings_file.exists():
            saved = json.loads(settings_file.read_text(encoding="utf-8"))
            result["branding"].update(saved.get("branding") or {})
            result["scoring"].update(saved.get("scoring") or {})
        return result

    def save_settings(cfg):
        with LOCK:
            temp = settings_file.with_suffix(".tmp")
            temp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(settings_file)

    def find_key(course, grade):
        if not grade:
            return None
        with connect() as conn:
            row = conn.execute("SELECT answers FROM answer_keys WHERE course=? AND grade=?", (course, grade)).fetchone()
        return json.loads(row[0]) if row else None

    def get_record(record_id):
        if not re.fullmatch(r"[a-f0-9]{32}", record_id):
            abort(404, description="Ficha no encontrada.")
        with connect() as conn:
            row = conn.execute("SELECT payload FROM records WHERE id=?", (record_id,)).fetchone()
        if not row:
            abort(404, description="Ficha no encontrada.")
        return json.loads(row[0])

    def put_record(record):
        record = _plain(record)
        with LOCK, connect() as conn:
            conn.execute("INSERT OR REPLACE INTO records(id,course,grade,confirmed,created,payload) VALUES(?,?,?,?,?,?)",
                         (record["id"], record["course"], record.get("grade"), int(record.get("confirmed", False)),
                          record["created"], json.dumps(record, ensure_ascii=False, allow_nan=False)))
        return record

    def all_records(course=None):
        with connect() as conn:
            query = "SELECT payload FROM records"
            params = ()
            if course:
                query += " WHERE course=?"
                params = (course,)
            rows = conn.execute(query + " ORDER BY created DESC, id DESC", params).fetchall()
        return [json.loads(row[0]) for row in rows]

    def public(record):
        result = {k: v for k, v in record.items() if k not in {"original_file", "warped_file", "annotated_file", "engine_reading"}}
        result["image_url"] = f"/api/records/{record['id']}/image"
        result["original_url"] = f"/api/records/{record['id']}/image?kind=original"
        return result

    def invalidate(course=None, grade=None, reason="La configuración de calificación cambió. Revise y confirme nuevamente."):
        for record in all_records(course):
            if grade and record.get("grade") != grade:
                continue
            record.update(confirmed=False, needs_review=True)
            record["issues"] = list(dict.fromkeys(record.get("issues", []) + [reason]))
            put_record(record)

    def engine_settings():
        cfg = settings()
        branding = cfg["branding"]
        for key in ("logo", "logo_left", "logo_right"):
            if branding.get(key):
                branding[key] = str(root / "branding" / Path(branding[key]).name)
        return cfg

    def annotate(record):
        from omr25 import annotate_reading
        reading = dict(record.get("engine_reading") or {})
        reading.update({k: record.get(k) for k in ("name", "school", "grade", "answers", "issues", "quality", "order", "time")})
        warp = root / "processed" / record["warped_file"]
        reading["warped_image"] = cv2.imdecode(np.frombuffer(warp.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        grading = record.get("grading")
        output = annotate_reading(reading, grading)
        if not isinstance(output, np.ndarray) or output.size == 0:
            raise ValueError("No se pudo generar la imagen de revisión.")
        ok, encoded = cv2.imencode(".jpg", output, [cv2.IMWRITE_JPEG_QUALITY, 94])
        if not ok:
            raise ValueError("No se pudo guardar la imagen calificada.")
        name = Path(record["warped_file"]).stem.replace("_alineada", "_calificada") + ".jpg"
        target = root / "processed" / name
        temp = target.with_suffix(".tmp")
        temp.write_bytes(encoded.tobytes())
        temp.replace(target)
        record["annotated_file"] = name

    def refresh_reading(record, reading):
        from omr25 import grade_answers
        clean = _plain(reading)
        for field in ("grading", "key_used", "scoring_used", "confirmed_at", "correct", "incorrect", "blank", "double", "score", "results"):
            record.pop(field, None)
        record.update(clean)
        record["engine_reading"] = clean
        record["confirmed"] = False
        course = record["course"]
        issues = list(record.get("issues") or [])
        if not record.get("name"):
            issues.append("Falta el nombre del alumno.")
        if not record.get("school"):
            issues.append("Falta el colegio.")
        if record.get("grade") not in range(1, 7):
            issues.append("El grado no se pudo determinar; selecciónelo en la revisión.")
        key = find_key(course, record.get("grade"))
        if key and "?" not in record.get("answers", []):
            grading = grade_answers(record["answers"], key, settings()["scoring"])
            record.update(_plain(grading))
            record["grading"] = _plain(grading)
            record["key_used"] = key
        else:
            issues.append("Falta una clave guardada para este grado." if not key else "Hay respuestas dudosas; revíselas antes de calificar.")
        record["issues"] = list(dict.fromkeys(str(issue) for issue in issues))
        record["needs_review"] = bool(record["issues"] or reading.get("needs_review"))


    def scan_image(data, filename, course):
        from omr25 import read_sheet, grade_answers
        if len(data) > 35 * 1024 * 1024:
            raise ValueError("La imagen supera el límite de 35 MB.")
        try:
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > 50_000_000:
                    raise ValueError("La imagen supera 50 megapíxeles; reduzca su tamaño.")
                image = ImageOps.exif_transpose(source).convert("RGB")
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=96)
                normalized = buffer.getvalue()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("El archivo no es una imagen válida.") from exc
        digest = hashlib.sha256(normalized).hexdigest()
        for previous in all_records(course):
            if previous.get("file_hash") == digest:
                return {**public(previous), "duplicate": True}
        record_id = uuid.uuid4().hex
        original_name = record_id + ".jpg"
        original_path = root / "uploads" / original_name
        original_path.write_bytes(normalized)
        try:
            reading = read_sheet(str(original_path))
            warp = reading.get("warped_image")
            if not isinstance(warp, np.ndarray) or warp.size == 0:
                raise ValueError("No se encontró una ficha A4 OMR 2.5. Revise las guías y el enfoque.")
            ok, encoded = cv2.imencode(".jpg", warp, [cv2.IMWRITE_JPEG_QUALITY, 97])
            if not ok:
                raise ValueError("No se pudo guardar la ficha alineada.")
            warped_name = record_id + "_alineada.jpg"
            (root / "processed" / warped_name).write_bytes(encoded.tobytes())
            clean = _plain(reading)
            record = {**clean, "id": record_id, "filename": Path(filename.replace("\\", "/")).name,
                      "source_path": "/".join(part for part in filename.replace("\\", "/").split("/") if part not in ("", ".", ".."))[-500:],
                      "course": course, "file_hash": digest, "confirmed": False, "created": datetime.now(timezone.utc).isoformat(),
                      "original_file": original_name, "warped_file": warped_name,
                      "engine_reading": clean, "order": "", "time": ""}
            refresh_reading(record, reading)
            annotate(record)
            return public(put_record(record))
        except Exception:
            original_path.unlink(missing_ok=True)
            for suffix in ("_alineada.jpg", "_calificada.jpg"):
                (root / "processed" / (record_id + suffix)).unlink(missing_ok=True)
            raise

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/sheet")
    def sheet():
        course = _course(request.args.get("curso", "Matematica"))
        return render_template("sheet.html", course=course, course_label=COURSES[course])

    @app.get("/download_sheet_pdf")
    def download_sheet_pdf():
        from omr25 import build_sheet_pdf
        course = _course(request.args.get("curso", "Matematica"))
        data, filename = build_sheet_pdf(curso=course, settings=engine_settings(), base_dir=str(BASE_DIR))
        return send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=request.args.get("inline") != "1", download_name=filename)

    @app.route("/api/settings", methods=["GET", "POST"])
    def api_settings():
        cfg = settings()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            title = str((data.get("branding") or {}).get("title", cfg["branding"]["title"])).strip()
            if len(title) > 80:
                raise ValueError("El título debe tener como máximo 80 caracteres.")
            scoring = data.get("scoring", cfg["scoring"])
            parsed = {}
            for field in ("buenas", "malas", "blanco"):
                try:
                    value = float(scoring[field])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("Ingrese los tres valores de calificación.") from exc
                if not math.isfinite(value) or abs(value) > 10000:
                    raise ValueError("Los valores de calificación deben ser números entre -10000 y 10000.")
                parsed[field] = value
            changed = parsed != cfg["scoring"]
            cfg["branding"]["title"] = title
            cfg["scoring"] = parsed
            save_settings(cfg)
            if changed:
                invalidate()
        cfg["courses"] = COURSES
        return jsonify(cfg)

    @app.route("/api/keys", methods=["GET", "POST"])
    def api_keys():
        data = request.get_json(silent=True) or {}
        course = _course(data.get("course") if request.method == "POST" else request.args.get("course", "Matematica"))
        if request.method == "POST":
            grade = _grade(data.get("grade"))
            key = _answers(data.get("answers"), key=True)
            previous = find_key(course, grade)
            with LOCK, connect() as conn:
                conn.execute("INSERT OR REPLACE INTO answer_keys(course,grade,answers) VALUES(?,?,?)", (course, grade, json.dumps(key)))
            if previous != key:
                invalidate(course, grade, "La clave de este grado cambió. Revise y confirme nuevamente.")
        return jsonify({"course": course, "keys": {str(g): find_key(course, g) for g in range(1, 7)}})

    @app.route("/api/logo/<slot>", methods=["POST", "DELETE"])
    def api_logo(slot):
        if slot not in {"left", "right"}:
            abort(404)
        cfg = settings()
        name = f"logo_{slot}.png"
        path = root / "branding" / name
        if request.method == "DELETE":
            path.unlink(missing_ok=True)
            cfg["branding"]["logo_" + slot] = ""
            if slot == "left":
                cfg["branding"]["logo"] = ""
        else:
            upload = request.files.get("file")
            if not upload:
                raise ValueError("Seleccione una imagen para el logo.")
            try:
                with Image.open(upload.stream) as source:
                    if source.width * source.height > 30_000_000:
                        raise ValueError("La imagen del logo es demasiado grande.")
                    source = ImageOps.exif_transpose(source).convert("RGBA")
                    source.thumbnail((1500, 450), Image.Resampling.LANCZOS)
                    output = Image.new("RGBA", (1500, 450), (255, 255, 255, 255))
                    output.alpha_composite(source, ((1500-source.width)//2, (450-source.height)//2))
                    temp = path.with_suffix(".tmp")
                    output.convert("RGB").save(temp, "PNG")
                    temp.replace(path)
            except (UnidentifiedImageError, OSError) as exc:
                raise ValueError("El logo debe ser una imagen PNG, JPEG o WebP válida.") from exc
            cfg["branding"]["logo_" + slot] = name
            if slot == "left":
                cfg["branding"]["logo"] = name
        save_settings(cfg)
        return jsonify(cfg)

    @app.get("/api/logo/<slot>/image")
    def logo_image(slot):
        if slot not in {"left", "right"}:
            abort(404)
        path = root / "branding" / f"logo_{slot}.png"
        if not path.exists():
            abort(404)
        return send_file(io.BytesIO(path.read_bytes()), mimetype="image/png")

    @app.post("/api/scan")
    def api_scan():
        course = _course(request.form.get("course", "Matematica"))
        uploads = request.files.getlist("files") or request.files.getlist("file")
        if not uploads:
            raise ValueError("Seleccione fotografías o un ZIP de fotografías.")
        accepted, errors = [], []
        count = 0
        total_bytes = 0
        for upload in uploads:
            filename = upload.filename or "imagen"
            try:
                raw = upload.read(180 * 1024 * 1024 + 1)
                if len(raw) > 180 * 1024 * 1024:
                    raise ValueError("El archivo supera 180 MB.")
                candidates = []
                if Path(filename).suffix.lower() == ".zip":
                    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                        members = [m for m in archive.infolist() if not m.is_dir() and Path(m.filename).suffix.lower() in IMAGE_EXTENSIONS]
                        if not members:
                            raise ValueError("El ZIP no contiene fotografías compatibles.")
                        if len(members) > 200:
                            raise ValueError("El ZIP admite como máximo 200 fotografías.")
                        for member in members:
                            parts = member.filename.replace("\\", "/").split("/")
                            if ".." in parts or member.filename.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", member.filename):
                                raise ValueError("El ZIP contiene una ruta no permitida.")
                            if member.file_size > 35 * 1024 * 1024 or (member.file_size > 5_000_000 and member.compress_size and member.file_size/member.compress_size > 100):
                                raise ValueError("Una fotografía del ZIP es demasiado grande.")
                            total_bytes += member.file_size
                            if total_bytes > 400 * 1024 * 1024:
                                raise ValueError("El contenido descomprimido supera 400 MB.")
                        candidates = [(m.filename, archive.read(m)) for m in members]
                elif Path(filename).suffix.lower() in IMAGE_EXTENSIONS:
                    candidates = [(filename, raw)]
                else:
                    raise ValueError("Use imágenes JPEG, PNG, WebP, TIFF o un ZIP de imágenes.")
                for candidate_name, data in candidates:
                    count += 1
                    if count > 200:
                        raise ValueError("Procese como máximo 200 fotografías por carga.")
                    try:
                        with LOCK:
                            accepted.append(scan_image(data, candidate_name, course))
                    except Exception as exc:
                        app.logger.info("No se pudo leer %s: %s", candidate_name, exc)
                        errors.append({"file": candidate_name.replace("\\", "/"), "error": str(exc)})
            except (ValueError, zipfile.BadZipFile, RuntimeError) as exc:
                errors.append({"file": filename, "error": str(exc)})
        return jsonify({"records": accepted, "errors": errors, "count": len(accepted)}), (200 if accepted else 422)

    @app.get("/api/records")
    def api_records():
        course = request.args.get("course")
        if course:
            course = _course(course)
        return jsonify({"records": [public(r) for r in all_records(course)]})

    def finalize_record(record_id, data):
        from omr25 import grade_answers
        record = get_record(record_id)
        if data.get("reviewed") is not True:
            raise ValueError("Revise la ficha y marque la casilla de confirmación.")
        name = str(data.get("name", record.get("name", ""))).strip().upper()
        school = str(data.get("school", record.get("school", ""))).strip().upper()
        if not name or not school:
            raise ValueError("Complete el nombre y el colegio antes de confirmar.")
        if "?" in name or "?" in school:
            raise ValueError("Resuelva las letras dudosas del nombre y del colegio antes de confirmar.")
        if len(name) > 120 or len(school) > 120:
            raise ValueError("El nombre y el colegio admiten como máximo 120 caracteres.")
        grade = _grade(data.get("grade", record.get("grade")))
        answers = _answers(data.get("answers", record.get("answers")))
        order = str(data.get("order", record.get("order", ""))).strip()
        time = str(data.get("time", record.get("time", ""))).strip()
        if order and not re.fullmatch(r"\d{1,6}", order):
            raise ValueError("El número de orden debe contener de 1 a 6 dígitos.")
        if time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
            raise ValueError("La hora debe tener formato de 24 horas HH:MM.")
        key = find_key(record["course"], grade)
        if not key:
            abort(409, description="Guarde primero la clave de este curso y grado.")
        grading = _plain(grade_answers(answers, key, settings()["scoring"]))
        record.update(name=name, school=school, grade=grade, answers=answers, order=order, time=time,
                      confirmed=True, needs_review=False, issues=[], grading=grading, key_used=key,
                      scoring_used=settings()["scoring"], confirmed_at=datetime.now(timezone.utc).isoformat())
        record.update(grading)
        annotate(record)
        put_record(record)
        return public(record)

    @app.post("/api/records/<record_id>/confirm")
    def confirm(record_id):
        with LOCK:
            result = finalize_record(record_id, request.get_json(silent=True) or {})
        return jsonify(result)

    @app.post("/api/records/confirm-clear")
    def confirm_clear():
        data = request.get_json(silent=True) or {}
        if data.get("reviewed") is not True:
            raise ValueError("Revise las lecturas claras del lote y marque la casilla antes de confirmar.")
        course = _course(data.get("course", "Matematica"))
        confirmed, errors = [], []
        with LOCK:
            pending = [r for r in all_records(course) if not r.get("confirmed")]
            candidates = []
            for record in pending:
                if record.get("needs_review") or record.get("issues"):
                    continue
                if not record.get("name") or not record.get("school"):
                    continue
                if "?" in record["name"] or "?" in record["school"]:
                    continue
                if record.get("grade") not in range(1, 7) or not find_key(course, record.get("grade")):
                    continue
                try:
                    answers = _answers(record.get("answers"))
                except ValueError:
                    continue
                if "X" in answers:
                    continue
                candidates.append(record)
            for record in candidates:
                try:
                    confirmed.append(finalize_record(record["id"], {"reviewed": True}))
                except (ValueError, HTTPException) as exc:
                    errors.append({"id": record["id"], "error": str(exc)})
        return jsonify({"records": confirmed, "count": len(confirmed), "excluded": len(pending)-len(candidates), "errors": errors})

    @app.post("/api/records/<record_id>/reprocess")
    def reprocess_record(record_id):
        from omr25 import read_sheet
        if (request.get_json(silent=True) or {}).get("reprocess") is not True:
            raise ValueError("Confirme que desea volver a leer esta fotografía.")
        with LOCK:
            previous = get_record(record_id)
            original = root / "uploads" / Path(previous["original_file"]).name
            reading = read_sheet(str(original))
            warp = reading.get("warped_image")
            if not isinstance(warp, np.ndarray) or warp.size == 0:
                raise ValueError("No se pudo alinear la foto; se conserva la lectura anterior.")
            ok, encoded = cv2.imencode(".jpg", warp, [cv2.IMWRITE_JPEG_QUALITY, 97])
            if not ok:
                raise ValueError("No se pudo guardar la nueva lectura.")
            record = dict(previous)
            refresh_reading(record, reading)
            name = record_id + "_alineada_" + uuid.uuid4().hex[:12] + ".jpg"
            target = root / "processed" / name
            temp = target.with_suffix(".tmp")
            temp.write_bytes(encoded.tobytes())
            temp.replace(target)
            record["warped_file"] = name
            record["reprocessed_at"] = datetime.now(timezone.utc).isoformat()
            try:
                annotate(record)
                result = public(put_record(record))
            except Exception:
                target.unlink(missing_ok=True)
                raise
        return jsonify(result)

    @app.delete("/api/records/<record_id>")
    def delete_record(record_id):
        record = get_record(record_id)
        with LOCK, connect() as conn:
            conn.execute("DELETE FROM records WHERE id=?", (record_id,))
        for folder, field in (("uploads", "original_file"), ("processed", "warped_file"), ("processed", "annotated_file")):
            if record.get(field):
                (root / folder / Path(record[field]).name).unlink(missing_ok=True)
        return jsonify({"ok": True})

    @app.get("/api/records/<record_id>/image")
    def record_image(record_id):
        record = get_record(record_id)
        kind = request.args.get("kind", "annotated")
        if kind not in {"annotated", "warped", "original"}:
            raise ValueError("Tipo de imagen no válido.")
        field = {"annotated": "annotated_file", "warped": "warped_file", "original": "original_file"}[kind]
        folder = "uploads" if kind == "original" else "processed"
        path = root / folder / Path(record.get(field) or "no-existe").name
        if not path.is_file():
            abort(404)
        return send_file(io.BytesIO(path.read_bytes()), mimetype="image/jpeg", as_attachment=request.args.get("download") == "1", download_name=f"ficha_{record_id[:8]}.jpg")

    @app.get("/api/export/excel")
    def download_excel():
        from excel25 import export_report
        course = _course(request.args.get("course", "Matematica"))
        records = [r for r in all_records(course) if r.get("confirmed")]
        if not records:
            return jsonify({"error": "Confirme al menos una ficha antes de exportar."}), 409
        data = export_report(records, course=course, scoring=settings()["scoring"])
        if isinstance(data, (str, Path)):
            data = Path(data).read_bytes()
        return send_file(io.BytesIO(data), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True,
                         download_name=f"OMR25_{course}_por_grados.xlsx")

    @app.get("/api/export/pdf")
    def download_batch_pdf():
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.utils import ImageReader
        course = _course(request.args.get("course", "Matematica"))
        records = [r for r in all_records(course) if r.get("confirmed")]
        if not records:
            return jsonify({"error": "Confirme al menos una ficha antes de exportar."}), 409
        buf = io.BytesIO()
        page_w, page_h = landscape(A4)
        pdf = canvas.Canvas(buf, pagesize=(page_w, page_h))
        pdf.setTitle(f"OMR 2.5 - Fichas calificadas - {COURSES[course]}")
        for record in sorted(records, key=lambda r: (r["grade"], r.get("name", ""))):
            path = root / "processed" / record["annotated_file"]
            image = ImageReader(str(path))
            iw, ih = image.getSize()
            scale = min(page_w / iw, page_h / ih)
            w, h = iw * scale, ih * scale
            pdf.drawImage(image, (page_w-w)/2, (page_h-h)/2, width=w, height=h)
            pdf.showPage()
        pdf.save()
        return send_file(io.BytesIO(buf.getvalue()), mimetype="application/pdf", as_attachment=True, download_name=f"OMR25_{course}_calificadas.pdf")

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify({"error": exc.description}), exc.code

    @app.errorhandler(ValueError)
    def validation_error(exc):
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        app.logger.exception("Error inesperado en OMR 2.5")
        return jsonify({"error": "No se pudo completar la operación. Sus fichas anteriores se conservan."}), 500

    return app


app = create_app()

if __name__ == "__main__":
    from iniciar_omr import main
    main(application=app)
