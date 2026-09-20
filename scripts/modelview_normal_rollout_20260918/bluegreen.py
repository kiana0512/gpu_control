"""Start an isolated candidate with exactly the live API environment/mounts.

Never prints environment values or persists them to a file. Does not change routing.
"""

import json
import subprocess

live = json.loads(subprocess.check_output(["docker", "inspect", "gpu-control-api-1"]))[0]
command = [
    "docker",
    "run",
    "-d",
    "--name",
    "gpu-control-api-normal-candidate",
    "--network",
    live["HostConfig"]["NetworkMode"],
    "--restart",
    "unless-stopped",
    "-p",
    "127.0.0.1:18018:8000",
]
for value in live["Config"]["Env"]:
    command.extend(["-e", value])
for mount in live["Mounts"]:
    command.extend(
        ["-v", mount["Source"] + ":" + mount["Destination"] + (":ro" if not mount["RW"] else "")]
    )
command.extend(["gpu-control-api:1.5.23-refcontrol-normal-20260918", *live["Config"]["Cmd"]])
subprocess.run(command, check=True)
