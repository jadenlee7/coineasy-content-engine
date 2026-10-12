"""CI uses the dedicated image, not generator credentials or its dependency set."""
from pathlib import Path
import yaml


def test_ci_installs_postgres_test_driver_without_replacing_generator_httpx():
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    install = next(step["run"] for step in data["jobs"]["python"]["steps"]
                   if step.get("name") == "Install Python dependencies")
    assert "psycopg[binary]==3.2.9" in install
    assert "requirements-final-card-preparation" not in install
    assert "httpx<0.28" in (root / "requirements.txt").read_text()
    job = data["jobs"]["final-preparation-image"]
    assert job["permissions"] == {"contents": "read"}
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "Dockerfile.final-card-preparation" in commands
    assert "--network none --read-only --cap-drop ALL" in commands
    assert "--validate-only" in commands
    assert '"public_send_attempted":false' in commands
    assert '"runtime_acl_verified":false' in commands
    assert "secrets." not in str(job)
