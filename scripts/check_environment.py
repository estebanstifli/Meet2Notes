from __future__ import annotations

import json
import platform
import shutil
import sys


def main() -> None:
    report = {
        "python": sys.version.split()[0],
        "python_supported": sys.version_info >= (3, 11),
        "platform": platform.platform(),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
