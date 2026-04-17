from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "generate_daily_report.py"


def _load_root_module():
    spec = importlib.util.spec_from_file_location("wechat_daily_report_root", ROOT_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load root script: {ROOT_SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


_ROOT_MODULE = _load_root_module()


def __getattr__(name: str):
    return getattr(_ROOT_MODULE, name)


def main() -> int:
    return _ROOT_MODULE.main()


if __name__ == "__main__":
    raise SystemExit(main())
