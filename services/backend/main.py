from dotenv import load_dotenv
load_dotenv()

import sys

import uvicorn

from src.config.settings import settings

if __name__ == "__main__":
    reload = "--reload" in sys.argv[1:]
    # Detrás de nginx/Vite: confiar en X-Forwarded-* para el esquema y la IP.
    uvicorn.run(
        "src.app:app",
        host="0.0.0.0",
        port=settings.BACKEND_PORT,
        proxy_headers=True,
        forwarded_allow_ips="*",
        reload=reload,
        reload_dirs=["src"] if reload else None,
    )
