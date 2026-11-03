"""Exercise the actual packaged executable without a key, models or inherited user state."""

import argparse
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = str(args.executable.resolve())
    environment = {key: value for key, value in os.environ.items() if key not in (
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "PYTHON_KEYRING_BACKEND")}
    environment["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    subprocess.run([executable, "--version"], env=environment, check=True, timeout=30)
    with tempfile.TemporaryDirectory(prefix="sopmaker-packaged-") as directory:
        root = Path(directory)
        environment["LOCALAPPDATA"] = str(root / "AppData")
        environment["XDG_STATE_HOME"] = str(root / "State")
        log = root / "startup.log"
        workspace = root / "Unicode workspace é"  # Exercise Windows path/space handling.
        with log.open("w", encoding="utf-8") as output:
            process = subprocess.Popen([executable, "demo", "--workspace", str(workspace), "--no-open"],
                                       stdout=output, stderr=output, env=environment)
            try:
                deadline = time.monotonic() + 40
                url = None
                while time.monotonic() < deadline:
                    text = log.read_text(encoding="utf-8", errors="replace")
                    for line in text.splitlines():
                        if line.startswith("Open: "):
                            url = line.removeprefix("Open: ")
                    if url:
                        break
                    if process.poll() is not None:
                        raise RuntimeError("Packaged app exited: " + text)
                    time.sleep(0.1)
                assert url, "Packaged app did not start"
                parsed = urlparse(url)
                token = parse_qs(parsed.fragment)["token"][0]
                origin = f"{parsed.scheme}://{parsed.netloc}"
                response = urlopen(Request(origin + "/api/state", headers={"Authorization": "Bearer " + token}), timeout=10)
                state = json.loads(response.read())
                assert len(state["observations"]) == 1
                assert not state["credentials"]["configured"]
                assert b"Review notes" in urlopen(origin, timeout=10).read()
                with urlopen(Request(origin + "/api/notebooks",
                                     data=json.dumps({"id": uuid.uuid4().hex, "name": "Packaged second notebook"}).encode(),
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}), timeout=10) as created:
                    child = urlparse(json.loads(created.read())["url"])
                with urlopen(Request(f"http://{child.netloc}/api/state",
                                     headers={"Authorization": "Bearer " + parse_qs(child.fragment)["token"][0]}), timeout=10) as blank:
                    assert json.loads(blank.read())["assets"] == []
                headers = {"Authorization": "Bearer " + parse_qs(child.fragment)["token"][0], "Content-Type": "application/json"}
                with urlopen(Request(f"http://{child.netloc}/api/session",
                                     data=b'{"timezone":"America/Toronto"}', headers=headers), timeout=10) as initialized:
                    assert initialized.status == 200
                with urlopen(Request(f"http://{child.netloc}/api/state", headers=headers), timeout=10) as snapshot:
                    selected = json.loads(snapshot.read())
                    assert selected["settings"]["timezone"] == "America/Toronto"
                    assert "America/Toronto" in selected["timezone_options"]
            finally:
                process.terminate()
                process.wait(timeout=20)
        subprocess.run([executable, "export", "--workspace", str(workspace), "--include-drafts"],
                       env=environment, check=True, timeout=30, capture_output=True)
        assert list((workspace / "exports").glob("*.zip"))
    print("Packaged version, startup, PyAV import, synthetic workspace, notebook creation, local server and export checks passed.")


if __name__ == "__main__":
    main()
