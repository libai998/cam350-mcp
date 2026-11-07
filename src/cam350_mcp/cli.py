"""Command line entry point for the CAM350 MCP server.

``cam350-mcp`` with no arguments speaks MCP over stdio and is what an MCP
client (Claude Code, Claude Desktop, ...) launches.  Utility flags help with
installation and diagnostics.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import sys

from . import __version__


def _win_to_wsl(path: str) -> str:
    p = path.replace("\\", "/")
    if len(p) > 2 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:]
    return p


def _check() -> dict:
    from . import core

    exe = core.EXE
    ref = core.reference_info()
    info = {
        "version": __version__,
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "cam350_exe": str(exe) if exe else None,
        "cam350_found": bool(exe and exe.exists()),
        "cam350_running": core.is_running(),
        "data_dir": str(core.BASE),
        "reference": ref["path"],
        "reference_exists": ref["available"],
    }
    if ref["available"]:
        info["reference_commands"] = ref.get("commands")
        info["reference_queries"] = ref.get("queries")
    else:
        info["reference_hint"] = ref.get("hint", core.REFERENCE_HINT)
    info["ok"] = info["cam350_found"]
    if not info["cam350_found"]:
        info["hint"] = (
            "未找到 camnt95.exe。请安装 CAM350，或设置环境变量 CAM350_EXE="
            r"C:\Program Files (x86)\DownStream Technologies\...\camnt95.exe"
        )
    return info


def _claude_command() -> dict:
    """Return registration commands for Windows and WSL clients."""
    py = sys.executable
    wsl_py = _win_to_wsl(py)
    return {
        "windows": f'claude mcp add --scope user cam350 -- "{py}" -m cam350_mcp.server',
        "wsl": f'claude mcp add --scope user cam350 -- "{wsl_py}" -m cam350_mcp.server',
        "windows_free_code": f'free-code mcp add --scope user cam350 -- "{py}" -m cam350_mcp.server',
        "wsl_free_code": f'free-code mcp add --scope user cam350 -- "{wsl_py}" -m cam350_mcp.server',
    }


def _utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr.

    Windows consoles default to a legacy code page (cp1252/cp936) and would
    raise UnicodeEncodeError on non-ASCII diagnostics.  JSON-RPC on stdio is
    UTF-8 as well, so this is safe for the server mode too.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="cam350-mcp",
        description="CAM350 12.x automation MCP server (stdio).",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("--check", action="store_true", help="print environment diagnostics as JSON")
    parser.add_argument(
        "--print-claude-command",
        action="store_true",
        help="print MCP registration commands for Claude Code (Windows and WSL)",
    )
    parser.add_argument("--home", help="override runtime/data directory (CAM350_MCP_HOME)")
    parser.add_argument("--exe", help="override camnt95.exe path (CAM350_EXE)")
    args = parser.parse_args(argv)

    if args.version:
        print(f"cam350-mcp {__version__}")
        return 0

    # Environment must be set before cam350_core is imported: it resolves
    # the install path and creates the data directory at import time.
    if args.home:
        os.environ["CAM350_MCP_HOME"] = args.home
    if args.exe:
        os.environ["CAM350_EXE"] = args.exe

    if args.check:
        print(json.dumps(_check(), ensure_ascii=False, indent=2))
        return 0

    if args.print_claude_command:
        cmds = _claude_command()
        print("# Windows (PowerShell / cmd)\n" + cmds["windows"])
        print("\n# WSL (Claude Code running in Linux)\n" + cmds["wsl"])
        print("\n# free-code variants\n" + cmds["windows_free_code"] + "\n" + cmds["wsl_free_code"])
        return 0

    from .server import mcp

    mcp.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
