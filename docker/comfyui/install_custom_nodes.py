#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

import yaml


def run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)  # noqa: S603 - fixed manifest-derived argv only


def main() -> None:
    lock_path = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    for item in lock.get("custom_nodes", []):
        if not item.get("enabled", False):
            continue
        required = {"name", "repository", "commit", "requirements_lock", "security_notes"}
        missing = required - item.keys()
        if missing:
            raise ValueError(f"custom node missing fields: {sorted(missing)}")
        target = destination / str(item["name"])
        run(["git", "clone", "--filter=blob:none", "--no-checkout", str(item["repository"]), str(target)])
        run(["/usr/bin/git", "checkout", "--detach", str(item["commit"])], target)
        completed = subprocess.run(  # noqa: S603 - fixed executable and validated target
            ["/usr/bin/git", "rev-parse", "HEAD"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        )
        if completed.stdout.strip() != str(item["commit"]):
            raise ValueError(f"custom node {item['name']} did not resolve to the pinned commit")
        requirement = lock_path.parent / str(item["requirements_lock"])
        run([sys.executable, "-m", "pip", "install", "--no-cache-dir", "--requirement", str(requirement)])


if __name__ == "__main__":
    main()
