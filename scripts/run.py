#!/usr/bin/env python3
"""便捷顶层入口：python run.py <subcommand> [args]，等价于 python -m apb <subcommand>。

让用户无需设 PYTHONPATH 即可从 scripts/ 目录运行。
"""
import sys
from pathlib import Path

# 把 scripts/ 加入 sys.path，使 `import apb` 可用
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apb.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
