"""depaudit 的自检测试。用独立临时项目目录。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from depaudit import audit, collect_imports, parse_requirements  # noqa: E402

ENTRY = os.path.join(BASE, "depaudit.py")


def _make_project(files: dict[str, str]) -> str:
    tmp = tempfile.mkdtemp(prefix="depaudit_proj_")
    for rel, content in files.items():
        path = Path(tmp) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp


def test_parse_requirements_basic() -> None:
    tmp = tempfile.mkdtemp()
    try:
        path = Path(tmp) / "requirements.txt"
        path.write_text("# 注释\nrequests==2.31.0\nnumpy>=1.20\n\n", encoding="utf-8")
        entries = parse_requirements(path)
        names = [e["name"] for e in entries]
        assert "requests" in names, names
        assert "numpy" in names, names
        assert len(entries) == 2, entries
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parse_requirements_skips_flags() -> None:
    tmp = tempfile.mkdtemp()
    try:
        path = Path(tmp) / "requirements.txt"
        path.write_text("-r other.txt\nrequests\n", encoding="utf-8")
        entries = parse_requirements(path)
        assert [e["name"] for e in entries] == ["requests"], entries
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_collect_imports_finds_modules() -> None:
    tmp = _make_project({
        "a.py": "import json\nimport requests\nfrom pathlib import Path\n",
    })
    try:
        result = collect_imports(Path(tmp))
        assert "requests" in result["used"], result
        assert "json" in result["used"], result
        assert "pathlib" in result["used"], result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_collect_imports_skips_relative() -> None:
    tmp = _make_project({
        "pkg/__init__.py": "",
        "pkg/mod.py": "from .helper import thing\nfrom ..other import x\n",
    })
    try:
        result = collect_imports(Path(tmp))
        assert "helper" not in result["used"], result
        assert "other" not in result["used"], result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_collect_imports_detects_dynamic() -> None:
    tmp = _make_project({
        "a.py": "import importlib\nmod = importlib.import_module('plugin_x')\n",
    })
    try:
        result = collect_imports(Path(tmp))
        assert len(result["dynamic"]) == 1, result
        assert result["dynamic"][0]["line"] == 2, result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_audit_detects_unused() -> None:
    tmp = _make_project({
        "requirements.txt": "requests==2.31.0\nunusedpkg==1.0\n",
        "a.py": "import requests\n",
    })
    try:
        result = audit(Path(tmp), None)
        unused = [u["name"] for u in result["unused"]]
        assert "unusedpkg" in unused, result
        assert "requests" not in unused, result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_audit_detects_undeclared() -> None:
    tmp = _make_project({
        "requirements.txt": "requests==2.31.0\n",
        "a.py": "import requests\nimport numpy\n",
    })
    try:
        result = audit(Path(tmp), None)
        undeclared = [u["name"] for u in result["undeclared"]]
        assert "numpy" in undeclared, result
        assert "requests" not in undeclared, result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_audit_ignores_stdlib_in_undeclared() -> None:
    tmp = _make_project({
        "requirements.txt": "",
        "a.py": "import json\nimport os\nimport sys\n",
    })
    try:
        result = audit(Path(tmp), None)
        assert result["undeclared"] == [], result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_audit_suggests_stdlib_alternative() -> None:
    tmp = _make_project({
        "requirements.txt": "requests==2.31.0\n",
        "a.py": "import requests\n",
    })
    try:
        result = audit(Path(tmp), None)
        assert result["replaceable"], result
        item = result["replaceable"][0]
        assert item["name"] == "requests", item
        assert item["alternative"] == "urllib.request", item
        assert item["cost"], "替代代价不能为空"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_audit_summary_counts() -> None:
    tmp = _make_project({
        "requirements.txt": "requests\nunused1\nunused2\n",
        "a.py": "import requests\n",
    })
    try:
        result = audit(Path(tmp), None)
        assert result["summary"]["declared_count"] == 3, result
        assert result["summary"]["unused_count"] == 2, result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_help_exits_zero() -> None:
    r = subprocess.run([sys.executable, ENTRY, "--help"], capture_output=True,
                       text=True, timeout=30, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert "depaudit" in r.stdout


def test_cli_reports_unused() -> None:
    tmp = _make_project({
        "requirements.txt": "requests\nunusedpkg\n",
        "a.py": "import requests\n",
    })
    try:
        r = subprocess.run([sys.executable, ENTRY, "--project", tmp],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        assert "unusedpkg" in r.stdout, r.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_json_output() -> None:
    tmp = _make_project({
        "requirements.txt": "requests\n",
        "a.py": "import requests\n",
    })
    try:
        r = subprocess.run([sys.executable, ENTRY, "--project", tmp, "--json"],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["summary"]["declared_count"] == 1, data
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_missing_project_exits_two() -> None:
    r = subprocess.run([sys.executable, ENTRY, "--project", "no_such_dir"],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 2, (r.returncode, r.stderr)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK  ] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n共 {len(tests)} 项，失败 {failed} 项")
    raise SystemExit(1 if failed else 0)
