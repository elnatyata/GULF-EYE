"""Run GulfEye:  python -m backend [--port 8765] [--host 127.0.0.1] [--reset]"""
import argparse
import os
import shutil

from . import config


def main():
    ap = argparse.ArgumentParser(description="GulfEye server")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--reset", action="store_true", help="delete the database and uploads, then seed the demo again")
    args = ap.parse_args()
    if args.reset and config.VAR.exists():
        for p in (config.DB_PATH, config.DB_PATH.with_suffix(".sqlite3-wal"), config.DB_PATH.with_suffix(".sqlite3-shm")):
            p.unlink(missing_ok=True)
        shutil.rmtree(config.UPLOADS, ignore_errors=True)
        print("database and uploads removed")
    import uvicorn
    print(f"GulfEye: http://{args.host}:{args.port}/   (demo password: {config.DEMO_PASSWORD})")
    uvicorn.run("backend.app:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
