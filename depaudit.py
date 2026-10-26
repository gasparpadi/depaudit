"""depaudit —— 按代码里实际的 import 情况审查依赖清单。

设计原则：
1. 以代码里的实际使用为准，不采信清单的自述。
2. 推荐替代品时必须同时说明它少了什么能力。
3. 不把依赖数量当成 KPI，删掉一个包也要给理由。

仅依赖 Python 标准库。

用法:
    python depaudit.py --help
    python depaudit.py --project .
    python depaudit.py --project . --manifest requirements.txt
    python depaudit.py --project . --json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# 标准库模块名（够用的子集，覆盖常见第三方会撞名的）
STDLIB = {
    "abc", "argparse", "array", "ast", "asyncio", "base64", "binascii", "bisect",
    "builtins", "bz2", "calendar", "collections", "concurrent", "configparser",
    "contextlib", "copy", "csv", "ctypes", "dataclasses", "datetime", "decimal",
    "difflib", "dis", "email", "enum", "errno", "faulthandler", "filecmp",
    "fileinput", "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "gzip", "hashlib", "heapq", "hmac", "html",
    "http", "imaplib", "importlib", "inspect", "io", "ipaddress", "itertools",
    "json", "keyword", "linecache", "locale", "logging", "lzma", "mailbox",
    "marshal", "math", "mimetypes", "mmap", "multiprocessing", "netrc",
    "numbers", "operator", "os", "pathlib", "pdb", "pickle", "pkgutil",
    "platform", "plistlib", "poplib", "posixpath", "pprint", "profile", "pstats",
    "pty", "pwd", "py_compile", "queue", "quopri", "random", "re", "readline",
    "reprlib", "resource", "secrets", "select", "selectors", "shelve", "shlex",
    "shutil", "signal", "site", "smtplib", "socket", "socketserver", "sqlite3",
    "ssl", "stat", "statistics", "string", "stringprep", "struct", "subprocess",
    "sys", "sysconfig", "tarfile", "tempfile", "textwrap", "threading", "time",
    "timeit", "tkinter", "token", "tokenize", "trace", "traceback", "tracemalloc",
    "types", "typing", "unicodedata", "unittest", "urllib", "uuid", "venv",
    "warnings", "wave", "weakref", "webbrowser", "xml", "xmlrpc", "zipfile",
    "zlib", "zoneinfo",
}

# 需求文件里的行：名字[版本] [其它]
REQ_LINE_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9._-]+)"
    r"(?:\s*(?P<extras>\[[^\]]*\]))?"
    r"(?:\s*(?P<op>[=<>!~]=|[<>])?\s*(?P<version>[0-9][^\s,;#]*)?)?"
    r"(?P<marker>;.*)?$"
)

# 常见第三方包 → 标准库替代方案，以及替代后失去什么
STDLIB_ALTERNATIVES = {
    "requests": ("urllib.request", "失去会话保持、连接池与第三方认证的便利封装"),
    "beautifulsoup4": ("html.parser", "失去 CSS 选择器与第三方标签库的兼容"),
    "bs4": ("html.parser", "失去 CSS 选择器与第三方标签库的兼容"),
    "pyyaml": ("json", "失去注释、多文档与自定义标签支持"),
    "yaml": ("json", "失去注释、多文档与自定义标签支持"),
    "six": ("（无需）", "Python 3 已有内置兼容层"),
    "dateutil": ("datetime", "失去自然语言日期解析与时区规则支持"),
    "python-dateutil": ("datetime", "失去自然语言日期解析与时区规则支持"),
    "pillow": ("（无需）", "失去图像处理能力，标准库没有等价物"),
    "numpy": ("array / statistics", "失去向量化运算与多维数组能力，性能显著下降"),
    "pandas": ("csv / sqlite3", "失去 DataFrame 语义与向量化操作"),
    "click": ("argparse", "失去命令分组与装饰器式声明"),
    "tqdm": ("（无需）", "失去进度条与耗时估算"),
    "colorama": ("（无需）", "Windows 终端颜色需要自行处理"),
    "chardet": ("（无需）", "只能靠编码尝试与统计推断，识别率下降"),
    "docutils": ("（无需）", "失去 docutils 的文档树模型与发布流水线"),
    "future": ("（无需）", "Python 3 已内置所需能力"),
    "mock": ("unittest.mock", "几乎等价，仅旧版本 mock 的接口略有差异"),
}


def parse_requirements(path: Path) -> list[dict]:
    """解析 requirements.txt 风格的清单。"""
    entries: list[dict] = []
    if not path.is_file():
        return entries
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = REQ_LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        if name in ("setuptools", "pip", "wheel"):
            continue
        entries.append({
            "name": name,
            "requirement": line,
            "version": m.group("version") or "",
        })
    return entries


def collect_imports(root: Path) -> dict:
    """扫描项目里的 Python 文件，统计顶层 import 的模块名。

    动态导入（importlib.import_module、__import__）单独记录，
    因为它们无法静态确定，不计入「未使用」。
    """
    used: dict[str, list[str]] = {}
    dynamic: list[dict] = []

    for path in sorted(root.rglob("*.py")):
        parts = path.parts
        if any(p in ("venv", ".venv", "site-packages", "node_modules",
                     "__pycache__", ".git", "build", "dist") for p in parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError, ValueError):
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    used.setdefault(top, []).append(rel)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue  # 相对导入
                if node.module:
                    top = node.module.split(".")[0]
                    used.setdefault(top, []).append(rel)
            elif isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in ("import_module", "__import__"):
                    dynamic.append({"file": rel, "line": node.lineno,
                                    "call": name})
    return {"used": used, "dynamic": dynamic}


def normalize(name: str) -> str:
    """把 import 名归一成包名：下划线与连字符等价。"""
    return name.lower().replace("-", "_")


def audit(project: Path, manifest: str | None) -> dict:
    """审查依赖清单。"""
    manifest_path = project / manifest if manifest else None
    if manifest_path is None:
        for candidate in ("requirements.txt", "pyproject.toml", "setup.py"):
            if (project / candidate).is_file():
                manifest_path = project / candidate
                break

    declared: list[dict] = []
    declared_names: set[str] = set()
    if manifest_path and manifest_path.name.endswith(".txt"):
        declared = parse_requirements(manifest_path)
        declared_names = {normalize(e["name"]) for e in declared}

    imports = collect_imports(project)
    used = imports["used"]
    used_norm = {normalize(k): k for k in used}

    # 清单里声明了但代码没 import
    unused = []
    for entry in declared:
        key = normalize(entry["name"])
        if key in used_norm:
            continue
        unused.append({
            "name": entry["name"],
            "requirement": entry["requirement"],
            "reason": "代码里没有找到对应的 import",
        })

    # 代码 import 了但清单里没声明（排除标准库与本地模块）
    undeclared = []
    for key, original in used_norm.items():
        if key in declared_names:
            continue
        if key in STDLIB:
            continue
        undeclared.append({
            "name": original,
            "files": sorted(set(used[original])),
        })

    # 可以换成标准库的
    replaceable = []
    for entry in declared:
        key = normalize(entry["name"])
        if key in STDLIB_ALTERNATIVES and key in used_norm:
            alternative, cost = STDLIB_ALTERNATIVES[key]
            replaceable.append({
                "name": entry["name"],
                "alternative": alternative,
                "cost": cost,
            })

    return {
        "project": str(project),
        "manifest": manifest_path.name if manifest_path else None,
        "declared": declared,
        "used_modules": sorted(used),
        "dynamic_imports": imports["dynamic"],
        "unused": unused,
        "undeclared": undeclared,
        "replaceable": replaceable,
        "summary": {
            "declared_count": len(declared),
            "used_module_count": len(used),
            "unused_count": len(unused),
            "undeclared_count": len(undeclared),
            "replaceable_count": len(replaceable),
            "dynamic_import_count": len(imports["dynamic"]),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="depaudit",
        description="按代码实际的 import 情况审查依赖清单（仅依赖标准库）",
        epilog=(
            "示例:\n"
            "  python depaudit.py --project .\n"
            "  python depaudit.py --project . --manifest requirements.txt\n"
            "  python depaudit.py --project . --json\n"
            "\n"
            "判断依据是代码里的 import 语句，不是清单本身。\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project", "-p", default=".", help="项目目录（默认当前目录）")
    ap.add_argument("--manifest", "-m", help="依赖清单文件名")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"找不到项目目录：{project}", file=sys.stderr)
        return 2

    result = audit(project, args.manifest)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    summary = result["summary"]
    print(f"项目：{result['project']}")
    print(f"清单：{result['manifest'] or '（未找到 requirements.txt）'}")
    print(f"声明依赖 {summary['declared_count']} 个，"
          f"代码实际 import {summary['used_module_count']} 个模块")
    print()

    if result["unused"]:
        print(f"清单里有 {len(result['unused'])} 个包在代码中找不到 import：")
        for item in result["unused"]:
            print(f"  - {item['name']:<24} {item['requirement']}")
            print(f"    {item['reason']}")
        print()

    if result["undeclared"]:
        print(f"代码 import 了 {len(result['undeclared'])} 个未在清单中声明的模块：")
        for item in result["undeclared"]:
            print(f"  - {item['name']:<24} 出现在 {', '.join(item['files'][:3])}")
        print()

    if result["replaceable"]:
        print(f"可以用标准库替代的依赖（{len(result['replaceable'])} 个）：")
        for item in result["replaceable"]:
            print(f"  - {item['name']} → {item['alternative']}")
            print(f"    代价：{item['cost']}")
        print()

    if result["dynamic_imports"]:
        print(f"检测到 {len(result['dynamic_imports'])} 处动态导入，"
              f"这些无法静态判定，未计入「未使用」：")
        for item in result["dynamic_imports"][:5]:
            print(f"  - {item['file']}:{item['line']}  {item['call']}")
        print()

    print("说明：删掉一个包前请确认——")
    print("  1. 它可能只在构建脚本或测试夹具里被用到；")
    print("  2. 它可能通过动态导入被加载；")
    print("  3. 移除它可能改变运行时行为。")
    print("减依赖本身不是目的，少装一个包省下来的时间常常抵不上排查成本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
