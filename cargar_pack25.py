"""Carga un ZIP o carpeta de fotos en la misma bandeja de revision de la app."""
import argparse
import io
from pathlib import Path

from app import app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Cargar fotografias OMR 2.5 para revisar y calificar.")
    parser.add_argument("origen", type=Path, help="ZIP, fotografia o carpeta de fotografias")
    parser.add_argument("--curso", choices=("Matematica", "Comunicacion"), default="Matematica")
    args = parser.parse_args(argv)
    source = args.origen.resolve()
    if not source.exists():
        parser.error("El archivo o carpeta no existe.")
    extensions = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".zip"}
    files = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in extensions) if source.is_dir() else [source]
    if not files:
        parser.error("No se encontraron fotografias ni ZIP compatibles.")
    client = app.test_client()
    loaded = errors = 0
    for file in files:
        with file.open("rb") as handle:
            result = client.post("/api/scan", data={"course": args.curso,
                                 "files": (handle, file.name)}, content_type="multipart/form-data")
        payload = result.get_json() or {}
        if result.status_code not in (200, 422):
            print(f"{file.name}: {payload.get('error', 'No se pudo cargar')}")
            errors += 1
            continue
        loaded += payload.get("count", 0)
        for issue in payload.get("errors", []):
            print(f"{issue['file']}: {issue['error']}")
            errors += 1
    print(f"\nFichas cargadas o ya existentes: {loaded}. Errores: {errors}.")
    print("Abra IniciarOMR.bat y vaya a Revisar. Los resultados se exportan despues de confirmar.")
    return 0 if loaded else 1


if __name__ == "__main__":
    raise SystemExit(main())
