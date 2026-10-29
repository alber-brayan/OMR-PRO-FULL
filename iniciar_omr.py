"""Inicio local con comprobacion de disponibilidad antes de abrir el navegador."""
import os
import socket
import threading
import time
import urllib.request
import webbrowser

from waitress import serve


def main(application=None):
    if application is None:
        from app import app as application
    host = os.environ.get("OMR_HOST", "0.0.0.0")
    port = int(os.environ.get("OMR_PORT", "5025"))
    url = f"http://127.0.0.1:{port}"
    with socket.socket() as probe:
        try:
            probe.bind((host, port))
        except OSError:
            try:
                import json
                with urllib.request.urlopen(url + "/api/settings", timeout=2) as response:
                    if json.load(response).get("version") == "2.5":
                        if os.environ.get("OMR_NO_BROWSER") != "1":
                            webbrowser.open(url)
                        return
            except (OSError, ValueError):
                pass
            raise SystemExit(f"El puerto {port} ya esta ocupado. Cierre la otra instancia o cambie OMR_PORT.")

    def open_when_ready():
        for _ in range(80):
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
            except OSError:
                time.sleep(0.25)

    if os.environ.get("OMR_NO_BROWSER") != "1":
        threading.Thread(target=open_when_ready, daemon=True).start()
    print(f"OMR 2.5 disponible en {url}. Para cerrar, presione Ctrl+C.", flush=True)
    if host == "0.0.0.0":
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect(("8.8.8.8", 80))
                address = route.getsockname()[0]
            print(f"Desde otro equipo de la misma red: http://{address}:{port}", flush=True)
        except OSError:
            print("Sin direccion de red disponible; conecte el equipo a su red local.", flush=True)
    serve(application, host=host, port=port, threads=4,
          max_request_body_size=200 * 1024 * 1024)


if __name__ == "__main__":
    main()
