"""Opt-in public YouTube integration check; uses an isolated session database."""
import json
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from main import create_app


def main():
    app = create_app(database=ROOT / "artifacts/youtube-check.sqlite3")
    stages = []
    with TestClient(app) as client:
        response = client.post("/api/imports", json={"kind": "youtube", "url": "https://youtu.be/c0SJgXUahEM"})
        response.raise_for_status()
        job_id = response.json()["id"]
        while True:
            job = client.get("/api/imports/" + job_id).json()
            if not stages or stages[-1] != job["state"]:
                stages.append(job["state"])
                print(job["state"], flush=True)
            if job["state"] in {"completed", "cancelled", "error"}:
                break
            time.sleep(1)
        assert job["state"] == "completed", job
        saved = client.get("/api/sessions/" + job["session_id"]).json()
        assert saved["session"]["source"]["original_url"] == "https://youtu.be/c0SJgXUahEM"
        assert any(sample.get("steering") is not None and sample.get("gear") is not None for sample in saved["samples"])
        report = {"stages": stages, "job": job, "sample_count": len(saved["samples"]),
                  "summary": saved["summary"], "isolated_database": "artifacts/youtube-check.sqlite3"}
        (ROOT / "artifacts/youtube-import-check.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
