"""Narrow source-package checks only; not a Docker build or hosted receipt."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKER = "Dockerfile.final-card-preparation"
REQUIREMENTS = "requirements-final-card-preparation.txt"
CA = "certs/supabase-prod-ca-2021.crt"
CA_SHA = "700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7"


def local_import_closure():
    """Include package initializers as well as direct/lazy repository imports."""
    todo = ["core.content_ops.final_card_preparation_runtime", "scripts.run_final_card_preparations"]
    seen = set()
    files = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        parts = name.split(".")
        path = ROOT.joinpath(*parts).with_suffix(".py")
        if not path.is_file():
            path = ROOT.joinpath(*parts, "__init__.py")
        if not path.is_file():
            continue
        seen.add(name)
        files.add(str(path.relative_to(ROOT)))
        todo.extend(".".join(parts[:n]) for n in range(1, len(parts)))
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imports = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = ".".join(parts[:-node.level]) if node.level else ""
                base = ".".join(item for item in (prefix, node.module) if item)
                imports = [base] + [base + "." + item.name for item in node.names]
            else:
                continue
            todo.extend(name for name in imports if name == "core" or name.startswith(("core.", "scripts.")))
    return files


class FinalPreparationPackagingTest(unittest.TestCase):
    def test_exact_narrow_closure_and_public_certificate(self):
        docker = (ROOT / DOCKER).read_text()
        copied = {}
        for line in docker.splitlines():
            if line.startswith("COPY "):
                _, source, destination = line.split()
                self.assertEqual(destination, "./" + source)
                self.assertNotIn("*", source)
                self.assertNotIn(source, copied)
                copied[source] = destination
        self.assertEqual(set(copied), local_import_closure() | {REQUIREMENTS, CA})
        self.assertEqual(hashlib.sha256((ROOT / CA).read_bytes()).hexdigest(), CA_SHA)
        self.assertIn(CA_SHA + "  " + CA, docker)
        self.assertIn("sha256sum -c -", docker)
        self.assertEqual((ROOT / REQUIREMENTS).read_text().splitlines(),
                         ["httpx==0.28.1", "psycopg[binary]==3.2.9"])
        ignored = (ROOT / (DOCKER + ".dockerignore")).read_text().splitlines()
        self.assertEqual(ignored[0], "**")
        actual_allow = set(ignored[1:])
        expected_allow = {"!" + item for item in set(copied) | {DOCKER}}
        expected_allow |= {"!" + str(parent) + "/" for item in copied
                           for parent in Path(item).parents if str(parent) != "."}
        self.assertEqual(actual_allow, expected_allow)
        self.assertNotIn("getUpdates", docker)
        self.assertFalse(any("publication_worker" in item or "quality_news" in item for item in copied))

    def test_stamp_off_bounded_manifest_and_no_schedule(self):
        docker = (ROOT / DOCKER).read_text()
        self.assertIn("ARG RAILWAY_GIT_COMMIT_SHA", docker)
        self.assertIn("^[a-f0-9]{40}$", docker)
        self.assertIn("printf '%s' \"$RAILWAY_GIT_COMMIT_SHA\" > /app/content-ops-build-sha", docker)
        self.assertIn("chmod 444 /app/content-ops-build-sha", docker)
        self.assertIn("CONTENT_OPS_FINAL_PREPARATION_ENABLED=false", docker)
        self.assertIn("USER nobody", docker)
        self.assertIn('CMD ["python", "-m", "scripts.run_final_card_preparations"]', docker)
        manifest = json.loads((ROOT / "railway.final-card-preparation.json").read_text())
        self.assertEqual(manifest["build"]["dockerfilePath"], DOCKER)
        self.assertEqual(manifest["deploy"], {
            "preDeployCommand": "python -m scripts.run_final_card_preparations --validate-only",
            "startCommand": "python -m scripts.run_final_card_preparations",
            "healthcheckPath": None, "cronSchedule": None, "restartPolicyType": "NEVER",
        })
        # autoDeploy is a separately read-back service-owner setting, not a
        # made-up Config-as-Code guarantee. This manifest installs no schedule.
        self.assertNotIn("autoDeploy", manifest)
        self.assertEqual(set(manifest["build"]["watchPatterns"]), {
            "/" + item for item in local_import_closure() | {
                DOCKER, DOCKER + ".dockerignore", REQUIREMENTS, CA,
                "railway.final-card-preparation.json",
            }
        })

    def test_staged_exact_closure_runs_off_without_database_or_network(self):
        with tempfile.TemporaryDirectory(prefix="final-preparation-package-") as directory:
            stage = Path(directory)
            for item in local_import_closure() | {CA}:
                destination = stage / item
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / item, destination)
            for args in ([], ["--validate-only"]):
                code = (
                    "import socket,psycopg; "
                    "deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError('external IO')); "
                    "socket.socket.connect=deny; psycopg.connect=deny; "
                    "from scripts.run_final_card_preparations import main; "
                    "raise SystemExit(main(" + repr(args) + "))"
                )
                result = subprocess.run([sys.executable, "-c", code], cwd=stage,
                    env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(stage)},
                    capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt["status"], "disabled")
                for key in ("enabled", "network_calls", "database_calls", "telegram_calls",
                            "public_send_attempted", "runtime_acl_verified", "release_pair_registered"):
                    self.assertIs(receipt[key], False)


if __name__ == "__main__":
    unittest.main()
