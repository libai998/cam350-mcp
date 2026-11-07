"""Core controller for CAM350 12.1 automation.

Running on Windows Python: drives camnt95.exe through its native macro
language (the officially supported automation interface).  Two execution
modes are supported:

* resident  - camnt95.exe is started once with a bootstrap+watcher macro.
              The watcher loops on a command file, so later macros are
              injected without restarting the application.
* batch     - camnt95.exe is started per call with ``-M<macro>`` and is
              asked to quit (``setquit@``) when the macro finishes.

No COM/DDE interface exists in CAM350 12.1; GUI automation is used only
for screenshots and for inspecting/dismissing dialogs.
"""

from __future__ import annotations

import ctypes
import csv
import io
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

APP_NAME = "cam350-mcp"
ENV_EXE = "CAM350_EXE"
ENV_HOME = "CAM350_MCP_HOME"
ENV_REFERENCE = "CAM350_MCP_REFERENCE"

REFERENCE_HINT = (
    "reference.json is an index of CAM350 help text and is not distributed "
    "with this repository.  If you have your own copy, point CAM350_MCP_REFERENCE "
    "at it to enable cam350_reference and macro command validation."
)


def data_dir() -> Path:
    """Writable per-user directory for runtime files.

    Override with ``CAM350_MCP_HOME``.  Defaults to
    ``%LOCALAPPDATA%\\cam350-mcp`` (the install script keeps the venv in
    ``%LOCALAPPDATA%\\Programs\\cam350-mcp`` so the two do not collide).
    """
    env = os.environ.get(ENV_HOME)
    if env:
        return Path(env).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


def _version_key(path: Path) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", str(path))]
    return tuple(nums) if nums else (0,)


def _registry_candidates() -> list[Path]:
    """Find camnt95.exe through installed-application registry entries."""
    found: list[Path] = []
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return found
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, subkey in roots:
        try:
            key = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(key, name) as item:
                        display = str(winreg.QueryValueEx(item, "DisplayName")[0])
                        if "cam350" not in display.lower():
                            continue
                        loc = str(winreg.QueryValueEx(item, "InstallLocation")[0] or "")
                        if loc:
                            p = Path(loc) / "camnt95.exe"
                            if p.exists():
                                found.append(p)
                except OSError:
                    continue
    return found


def find_cam350() -> Path | None:
    """Locate camnt95.exe.

    Order: ``CAM350_EXE`` env var, registry, then common install trees
    (highest version wins).
    """
    env = os.environ.get(ENV_EXE)
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else p  # report the configured path even if wrong
    candidates = _registry_candidates()
    roots = [
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        r"C:\Program Files (x86)",
        r"C:\Program Files",
    ]
    patterns = [
        "DownStream Technologies/*/*/camnt95.exe",
        "DownStream Technologies/*/camnt95.exe",
        "CAM350*/camnt95.exe",
    ]
    for root in dict.fromkeys(r for r in roots if r):
        base = Path(root)
        if not base.is_dir():
            continue
        for pattern in patterns:
            candidates.extend(base.glob(pattern))
    existing = [p for p in candidates if p.is_file()]
    if not existing:
        return None
    return max(existing, key=_version_key)


BASE = data_dir()
RUNTIME = BASE / "runtime"
CALLS = RUNTIME / "calls"
SHOTS = RUNTIME / "shots"
STAGING = RUNTIME / "staging"

EXE = find_cam350()
EXE_DIR = EXE.parent if EXE else None

WATCHER = RUNTIME / "watcher.scr"
BOOT_TSV = RUNTIME / "boot.tsv"
BOOT_DONE = RUNTIME / "boot_done.flag"
CMD = RUNTIME / "cmd.scr"
READY = RUNTIME / "cmd.ready"
QUIT = RUNTIME / "quit.flag"
STATE = RUNTIME / "state.json"
LOG = RUNTIME / "mcp.log"

def _default_reference() -> Path:
    """Locate the optional CAM350 help index.

    It is not redistributed with this project; users may supply their own copy
    through ``CAM350_MCP_REFERENCE`` or by placing ``reference.json`` next to
    this module (or inside the installed package).
    """
    env = os.environ.get(ENV_REFERENCE)
    if env:
        return Path(env).expanduser()
    try:
        from importlib.resources import files as _resource_files

        return Path(str(_resource_files("cam350_mcp") / "reference.json"))
    except Exception:  # pragma: no cover - source checkout fallback
        return Path(__file__).with_name("reference.json")


REFERENCE = _default_reference()

for d in (RUNTIME, CALLS, SHOTS, STAGING):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("cam350")


def require_exe() -> Path:
    if EXE is None:
        raise FileNotFoundError(
            "未找到 CAM350（camnt95.exe）。请安装 CAM350，"
            f"或设置环境变量 {ENV_EXE} 指向 camnt95.exe"
        )
    if not EXE.exists():
        raise FileNotFoundError(f"CAM350 路径不存在: {EXE}")
    return EXE

_LOCK = threading.RLock()

LAYER_TYPES = {
    0: "Top",
    1: "Internal",
    2: "NegPlane",
    3: "Bottom",
    4: "Graphic",
    6: "Border",
    7: "SilkTop",
    8: "SilkBottom",
    11: "MaskTop",
    12: "MaskBottom",
    13: "Temporary",
    14: "PosPlane",
    16: "RefDesTop",
    17: "RefDesBottom",
    18: "Insulator",
    19: "PasteTop",
    20: "PasteBottom",
    21: "NCData",
}
COPPER_TYPES = {0, 1, 2, 3, 14}
NC_TYPES = {21}


# --------------------------------------------------------------------------
# path helpers
# --------------------------------------------------------------------------


def win_path(value: str | os.PathLike, *, must_exist: bool = False) -> str:
    """Normalise a path supplied by the caller to a Windows path.

    Accepts native Windows paths and WSL paths under /mnt/<drive>/.  Other
    WSL paths cannot be reached by the Windows application.
    """
    p = str(value).strip().strip('"')
    if not p:
        raise ValueError("empty path")
    low = p.lower()
    if low.startswith("/mnt/") and len(p) > 6 and p[5].isalpha() and p[6] in ("/", "\\"):
        drive = p[5].upper()
        rest = p[6:].replace("/", "\\")
        p = f"{drive}:{rest}"
    elif re.match(r"^[a-zA-Z]:[\\/]", p):
        p = p.replace("/", "\\")
    elif p.startswith("\\\\"):
        p = p.replace("/", "\\")
    else:
        raise ValueError(
            f"路径必须在 Windows 盘符或 /mnt/<盘符>/ 下，无法从 Windows 端访问: {value!r}"
        )
    if must_exist and not Path(p).exists():
        raise FileNotFoundError(p)
    return p


def q(value: str | os.PathLike) -> str:
    """Quote a path for use inside a CAM350 macro string literal."""
    return '"' + str(value).replace('"', "'") + '"'


def safe_name(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", name).strip(" ._")
    return name or "layer"


def write_macro(path: Path, lines: list[str]) -> None:
    """Write an ANSI/CRLF macro file (CAM350 is an ANSI application)."""
    text = "\r\n".join(lines) + "\r\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        data = text.encode("mbcs", errors="replace")
    except LookupError:  # pragma: no cover - non-Windows fallback
        data = text.encode("utf-8")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="mbcs", errors="replace")
    except LookupError:  # pragma: no cover
        return path.read_text(encoding="utf-8", errors="replace")


def _touch(path: Path, text: str = "x") -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="ascii")
    os.replace(tmp, path)


def _clear(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("cannot remove %s", p)


# --------------------------------------------------------------------------
# Win32 helpers
# --------------------------------------------------------------------------

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def pids() -> list[int]:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq camnt95.exe", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=15,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        ).stdout
    except Exception as exc:  # pragma: no cover
        log.warning("tasklist failed: %s", exc)
        return []
    result = []
    for row in csv.reader(io.StringIO(out)):
        if len(row) >= 2 and row[0].lower() == "camnt95.exe":
            try:
                result.append(int(row[1]))
            except ValueError:
                pass
    return result


def is_running() -> bool:
    return bool(pids())


def _window_text(hwnd: int) -> str:
    n = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _activate_window(hwnd: int) -> bool:
    """Bring a window to the foreground, bypassing ForegroundLockTimeout."""
    try:
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE (no-op if already normal)
        fg = _user32.GetForegroundWindow()
        fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
        my_tid = _kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid and fg_tid != my_tid:
            attached = bool(_user32.AttachThreadInput(my_tid, fg_tid, True))
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        _user32.SetFocus(hwnd)
        if attached:
            _user32.AttachThreadInput(my_tid, fg_tid, False)
        time.sleep(0.5)
        return _user32.GetForegroundWindow() == hwnd
    except Exception as exc:  # pragma: no cover
        log.warning("activate window failed: %s", exc)
        return False


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def windows(pid: int, *, visible_only: bool = True) -> list[dict]:
    found: list[dict] = []

    def cb(hwnd, _lparam):
        p = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid:
            return True
        visible = bool(_user32.IsWindowVisible(hwnd))
        if visible_only and not visible:
            return True
        rect = _window_rect(hwnd)
        found.append(
            {
                "hwnd": int(hwnd),
                "visible": visible,
                "title": _window_text(hwnd),
                "class": _class_name(hwnd),
                "rect": rect,
            }
        )
        return True

    _user32.EnumWindows(_EnumWindowsProc(cb), 0)
    return found


def main_window(pid: int | None = None) -> dict | None:
    for pid_ in ([pid] if pid else pids()):
        wins = windows(pid_)
        for w in wins:
            if "CAM350/DFMSTREAM" in w["title"]:
                return w
        # fall back to largest visible Afx window
        candidates = [w for w in wins if w["class"].startswith("Afx") and w["rect"]]
        if candidates:
            candidates.sort(
                key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]),
                reverse=True,
            )
            return candidates[0]
    return None


def _buttons(hwnd: int) -> list[dict]:
    found: list[dict] = []

    def cb(child, _lparam):
        if _class_name(child).lower() == "button":
            found.append(
                {
                    "hwnd": int(child),
                    "text": _window_text(child).replace("&", "").strip(),
                    "visible": bool(_user32.IsWindowVisible(child)),
                }
            )
        return True

    _user32.EnumChildWindows(hwnd, _EnumChildProc(cb), 0)
    return found


def list_dialogs() -> list[dict]:
    """Visible top-level windows (main window + dialogs) with button texts."""
    result = []
    for pid in pids():
        for w in windows(pid):
            cls = w["class"]
            if cls in ("tooltips_class32", "MSCTFIME UI", "IME", "Internet Explorer_Hidden"):
                continue
            if w["title"] or cls == "#32770":
                w = dict(w)
                w["pid"] = pid
                w["buttons"] = _buttons(w["hwnd"])
                result.append(w)
    return result


def click_dialog_button(text: str, title_substr: str | None = None) -> dict:
    """Click a button in a CAM350 dialog using BM_CLICK (does not need focus)."""
    text_l = text.lower()
    for dlg in list_dialogs():
        if title_substr and title_substr.lower() not in dlg["title"].lower():
            continue
        for btn in dlg["buttons"]:
            if text_l in btn["text"].lower():
                code = wintypes.DWORD()
                _user32.SendMessageTimeoutW(
                    btn["hwnd"], 0x00F5, 0, 0, 0x0002, 3000, ctypes.byref(code)
                )
                return {"clicked": True, "dialog": dlg["title"], "button": btn["text"]}
    return {"clicked": False, "error": f"button {text!r} not found"}


def screenshot(name: str | None = None, focus: bool = True) -> Path:
    from PIL import ImageGrab

    main = main_window()
    if main and focus:
        _activate_window(main["hwnd"])

    rects = []
    for dlg in list_dialogs():
        r = dlg.get("rect")
        if r and r[2] - r[0] > 40 and r[3] - r[1] > 40:
            rects.append(r)
    if rects:
        left = min(r[0] for r in rects)
        top = min(r[1] for r in rects)
        right = max(r[2] for r in rects)
        bottom = max(r[3] for r in rects)
    else:
        left, top, right, bottom = (0, 0, 0, 0)

    SHOTS.mkdir(parents=True, exist_ok=True)
    out = SHOTS / (name or f"cam350_{datetime.now():%Y%m%d_%H%M%S}.png")
    if rects:
        pad = 4
        img = ImageGrab.grab(
            bbox=(max(0, left - pad), max(0, top - pad), right + pad, bottom + pad),
            all_screens=True,
        )
    else:
        img = ImageGrab.grab(all_screens=True)
    img.save(out)
    return out


# --------------------------------------------------------------------------
# process / state
# --------------------------------------------------------------------------

def read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(data: dict) -> None:
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _parse_tsv(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    rows = []
    for line in read_text(path).splitlines():
        if not line.strip():
            continue
        rows.append([c.strip() for c in line.split("\t")])
    return rows


def _boot_state() -> dict:
    state = {"boot_time": datetime.now().isoformat(timespec="seconds")}
    for row in _parse_tsv(BOOT_TSV):
        if len(row) >= 2:
            state[row[0]] = row[1] if len(row) == 2 else row[1:]
    if "numlayers" in state:
        try:
            state["numlayers"] = int(state["numlayers"])
        except Exception:
            pass
    for k in ("dbminx", "dbminy", "dbmaxx", "dbmaxy"):
        if k in state:
            try:
                state[k] = float(state[k])
            except Exception:
                pass
    if "units" in state:
        try:
            state["units_name"] = "metric" if int(state["units"]) == 1 else "english"
        except Exception:
            pass
    return state


def _wait_for(predicate, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _watcher_script(open_file: str | None, import_dir: str | None, save_as: str | None) -> list[str]:
    lines: list[str] = []
    if open_file:
        lines.append(f"setopenfile@ {q(open_file)}")
    if import_dir:
        lines.append(f"auto_import@ {q(import_dir)}")
        lines.append("update_layerbar@")
        lines.append("update_dcodebar@")
    if (open_file or import_dir):
        lines.extend(
            [
                f'OPEN {q(BOOT_TSV)} FOR OUTPUT AS #200',
                'PRINT #200, "dbname", dbname!',
                'PRINT #200, "units", Units!',
                'PRINT #200, "numlayers", numlayers!',
                'PRINT #200, "dbminx", dbminx!',
                'PRINT #200, "dbminy", dbminy!',
                'PRINT #200, "dbmaxx", dbmaxx!',
                'PRINT #200, "dbmaxy", dbmaxy!',
                "CLOSE #200",
            ]
        )
    if save_as:
        lines.append(f"setsaveasfile@ {q(save_as)}")
    lines.extend(
        [
            f'OPEN {q(BOOT_DONE)} FOR OUTPUT AS #201',
            'PRINT #201, "boot ok"',
            "CLOSE #201",
            "done = 0",
            "DO",
            f'IF FILEEXISTS({q(QUIT)}) = 1 THEN',
            "done = 1",
            "ELSE",
            f'IF FILEEXISTS({q(READY)}) = 1 THEN',
            f"MACRO_PLAY {q(CMD)}",
            f"KILL {q(READY)}",
            "END IF",
            "END IF",
            "DELAY 250",
            "LOOP UNTIL done = 1",
            "setquit@",
        ]
    )
    return lines


def start(
    job: str | None = None,
    import_dir: str | None = None,
    save_as: str | None = None,
    timeout: float = 90,
) -> dict:
    """Start a resident CAM350 instance (optionally opening/importing data)."""
    with _LOCK:
        _clear(QUIT, READY, CMD, BOOT_TSV, BOOT_DONE)
        if is_running():
            return {"started": False, "error": "CAM350 已在运行；如需换文件请用 cam350_open_job 或先 stop"}
        try:
            exe = require_exe()
        except FileNotFoundError as exc:
            return {"started": False, "error": str(exc)}

        open_file = win_path(job, must_exist=True) if job else None
        imp = win_path(import_dir, must_exist=True) if import_dir else None
        save = win_path(save_as) if save_as else None
        if open_file and not open_file.lower().endswith(".cam"):
            # non .cam file: treat its parent folder as an auto-import source
            imp = imp or str(Path(open_file).parent)
            open_file = None
        if imp and save is None:
            # importing without a target job: keep data unsaved, caller can save
            pass

        write_macro(WATCHER, _watcher_script(open_file, imp, save))
        log.info("starting CAM350: job=%s import=%s save=%s", open_file, imp, save)
        subprocess.Popen(
            [str(exe), f"-M{WATCHER}"],
            cwd=str(exe.parent),
            close_fds=True,
        )
        ok = _wait_for(lambda: BOOT_DONE.exists() and is_running(), timeout)
        state: dict = {"started": bool(ok), "running": is_running(), "mode": "resident"}
        if ok:
            boot = _boot_state()
            state.update(boot)
            state["job"] = open_file or boot.get("dbname") or (save if save else None)
            _write_state(
                {
                    "job": state.get("job"),
                    "units": boot.get("units_name"),
                    "numlayers": boot.get("numlayers"),
                    "started": state["started"],
                }
            )
            state["boot"] = boot
        else:
            state["warning"] = f"启动后 {timeout:.0f}s 内未完成引导宏；可能有启动/许可对话框"
            try:
                state["screenshot"] = str(screenshot(name="start_timeout.png", focus=False))
            except Exception:
                pass
        return state


def stop(timeout: float = 15, force: bool = False) -> dict:
    with _LOCK:
        procs = pids()
        if not procs:
            _clear(QUIT, READY, CMD, BOOT_TSV, BOOT_DONE)
            return {"stopped": True, "message": "CAM350 未运行"}
        _touch(QUIT)
        if _wait_for(lambda: not pids(), timeout):
            _clear(QUIT, READY, CMD, BOOT_TSV, BOOT_DONE)
            return {"stopped": True, "graceful": True}
        if force:
            for pid in pids():
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    timeout=15,
                    creationflags=0x08000000,
                )
            _clear(QUIT, READY, CMD, BOOT_TSV, BOOT_DONE)
            return {"stopped": not pids(), "graceful": False, "forced": True}
        return {
            "stopped": False,
            "error": f"{timeout:.0f}s 内未退出；可能有保存确认对话框。可用 cam350_dialogs 查看，或 force=true 强制结束",
            "pids": pids(),
        }


def abort() -> dict:
    """Interrupt a stuck macro, or kill the process if it cannot be interrupted."""
    with _LOCK:
        for pid in pids():
            for w in windows(pid):
                if "macro" in w["title"].lower():
                    res = click_dialog_button("OK", title_substr=w["title"])
                    if res.get("clicked"):
                        return {"interrupted": True, "dialog": w["title"], **res}
        return {"interrupted": False, **stop(timeout=2, force=True)}


# --------------------------------------------------------------------------
# macros
# --------------------------------------------------------------------------

_CMD_RE = re.compile(r"\b([a-z][a-z0-9_]*@)")
_QUERY_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)!")


def _reference_sets() -> tuple[set[str], set[str]] | None:
    """Return known commands/queries, or None when no reference is installed."""
    try:
        data = json.loads(REFERENCE.read_text(encoding="utf-8"))
        commands = {c["command"].lower() for c in data.get("commands", [])}
        queries = {q["query"].lower() for q in data.get("queries", [])}
        return commands, queries
    except Exception:
        return None


def validate(script: str) -> list[str]:
    sets = _reference_sets()
    if sets is None:
        return []  # no reference index: validation is skipped, macros still run
    commands, queries = sets
    warnings = []
    unknown_cmd = sorted({c for c in _CMD_RE.findall(script.lower()) if c not in commands})
    unknown_q = sorted({q.lower() + "!" for q in _QUERY_RE.findall(script) if (q + "!").lower() not in queries})
    if unknown_cmd:
        warnings.append("未在帮助文档中找到的命令（仍会执行，请确认拼写）: " + ", ".join(unknown_cmd[:20]))
    if unknown_q:
        warnings.append("未在帮助文档中找到的查询变量（可能让宏中断）: " + ", ".join(unknown_q[:20]))
    return warnings


def _strip_tail(script: str) -> tuple[str, list[str]]:
    """Remove trailing END / EXIT MACRO / quit commands (handled by wrapper)."""
    lines = script.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    removed = []
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        core = re.sub(r"^\d+\s+", "", last).strip().lower()
        if core in ("end", "exit macro", "setquit@", "quit@"):
            removed.append(core)
            lines.pop()
            continue
        break
    return "\n".join(lines).strip(), removed


def run_macro(script: str, timeout: float = 180, tag: str = "macro") -> dict:
    """Run a macro in the resident instance (starting a blank one if needed)."""
    with _LOCK:
        if not is_running():
            start()
        if not is_running():
            return {"ok": False, "error": "无法启动 CAM350"}
        if not main_window():
            return {"ok": False, "error": "CAM350 进程存在但没有主窗口"}

        call_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}_{safe_name(tag)}"
        call_dir = CALLS / call_id
        call_dir.mkdir(parents=True, exist_ok=True)
        done = call_dir / "done.flag"
        progress = call_dir / "progress.log"

        body, removed = _strip_tail(script)
        wrapped = [
            f"OPEN {q(progress)} FOR APPEND AS #200",
            'PRINT #200, "BEGIN"',
            "CLOSE #200",
        ]
        wrapped.extend(body.split("\n") if body else [])
        wrapped.extend(
            [
                f"OPEN {q(progress)} FOR APPEND AS #200",
                'PRINT #200, "DONE"',
                "CLOSE #200",
                f"OPEN {q(done)} FOR OUTPUT AS #201",
                'PRINT #201, "done"',
                "CLOSE #201",
            ]
        )

        warnings = validate(body)
        write_macro(CMD, wrapped)
        _clear(READY)
        _touch(READY)

        ok = _wait_for(done.exists, timeout)
        alive = is_running()
        logtext = read_text(progress) if progress.exists() else ""
        result = {
            "ok": bool(ok),
            "mode": "resident",
            "call_id": call_id,
            "call_dir": str(call_dir),
            "log": logtext[-4000:],
            "removed_tail": removed,
            "warnings": warnings,
            "warnings_text": "；".join(warnings),
        }
        if ok:
            _clear(READY)
        else:
            result["error"] = "宏在超时前未完成；可能有语法错误或模态对话框"
            result["pids"] = pids()
            try:
                result["screenshot"] = str(screenshot(name=f"{call_id}_timeout.png", focus=False))
            except Exception:
                pass
            result["hint"] = "可用 cam350_dialogs 查看窗口/按钮，cam350_abort 中断或重启"
        return result


def batch_macro(
    script: str,
    timeout: float = 300,
    quit: bool = True,
    force: bool = False,
    tag: str = "batch",
) -> dict:
    """Run a macro in a fresh CAM350 instance (``-M<macro>``)."""
    with _LOCK:
        if is_running():
            if not force:
                return {
                    "ok": False,
                    "error": "已有 CAM350 实例在运行。CAM350 不支持并行会话；请先 cam350_stop，或传 force=true",
                }
            stop(force=True, timeout=5)
        try:
            exe = require_exe()
        except FileNotFoundError as exc:
            return {"ok": False, "error": str(exc)}

        call_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}_{safe_name(tag)}"
        call_dir = CALLS / call_id
        call_dir.mkdir(parents=True, exist_ok=True)
        done = call_dir / "done.flag"
        progress = call_dir / "progress.log"
        macro_path = call_dir / "batch.scr"

        body, removed = _strip_tail(script)
        lines = [
            f"OPEN {q(progress)} FOR APPEND AS #200",
            'PRINT #200, "BEGIN"',
            "CLOSE #200",
        ]
        lines.extend(body.split("\n") if body else [])
        lines.extend(
            [
                f"OPEN {q(done)} FOR OUTPUT AS #201",
                'PRINT #201, "done"',
                "CLOSE #201",
            ]
        )
        if quit:
            lines.append("setquit@")
        warnings = validate(body)
        write_macro(macro_path, lines)

        proc = subprocess.Popen(
            [str(exe), f"-M{macro_path}"],
            cwd=str(exe.parent),
            close_fds=True,
        )
        ok = _wait_for(done.exists, timeout)
        exited = proc.poll() is not None
        if quit and ok and not exited:
            _wait_for(lambda: proc.poll() is not None, 20)

        logtext = read_text(progress) if progress.exists() else ""
        result = {
            "ok": bool(ok),
            "mode": "batch",
            "call_id": call_id,
            "call_dir": str(call_dir),
            "pid": proc.pid,
            "exit_code": proc.poll(),
            "log": logtext[-4000:],
            "removed_tail": removed,
            "warnings": warnings,
            "warnings_text": "；".join(warnings),
        }
        if not ok:
            result["error"] = f"宏在 {timeout:.0f}s 内未完成"
            result["hint"] = "若进程仍在，可用 cam350_dialogs 查看；cam350_abort 结束它"
            if proc.poll() is None:
                try:
                    result["screenshot"] = str(screenshot(name=f"{call_id}_timeout.png", focus=False))
                except Exception:
                    pass
        return result


# --------------------------------------------------------------------------
# CAM data helpers
# --------------------------------------------------------------------------

def query(expressions: list[str], timeout: float = 60) -> dict:
    exprs = []
    for e in expressions:
        e = e.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*!", e):
            raise ValueError(f"只接受查询变量（以 ! 结尾），例如 numlayers!、dbname!: {e!r}")
        exprs.append(e)
    if not exprs:
        return {}
    out = CALLS / f"query_{uuid.uuid4().hex[:8]}.tsv"
    lines = [f"OPEN {q(out)} FOR OUTPUT AS #200"]
    for i, e in enumerate(exprs):
        lines.append(f'PRINT #200, "{i:02d}:{e}", {e}')
    lines.append("CLOSE #200")
    res = run_macro("\n".join(lines), timeout=timeout, tag="query")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "log": res.get("log"), "warnings": res.get("warnings")}
    values = {}
    for row in _parse_tsv(out):
        if len(row) >= 2:
            key = row[0].split(":", 1)[-1]
            values[key] = row[1] if len(row) == 2 else row[1:]
    try:
        out.unlink()
    except OSError:
        pass
    return {"ok": True, "values": values}


def layers(timeout: float = 60) -> dict:
    out = CALLS / f"layers_{uuid.uuid4().hex[:8]}.tsv"
    lines = [
        f"OPEN {q(out)} FOR OUTPUT AS #200",
        'PRINT #200, "units", Units!',
        'PRINT #200, "numlayers", numlayers!',
        "n% = numlayers!",
        "FOR i = 0 TO n% - 1",
        "setlayer@ i",
        'PRINT #200, "layer", i, layername!, layertype!',
        "NEXT",
        "CLOSE #200",
    ]
    res = run_macro("\n".join(lines), timeout=timeout, tag="layers")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "log": res.get("log"), "warnings": res.get("warnings")}
    data = {"ok": True, "units": None, "numlayers": None, "layers": []}
    for row in _parse_tsv(out):
        if row[0] == "units" and len(row) >= 2:
            data["units"] = int(row[1])
            data["units_name"] = "metric" if data["units"] == 1 else "english"
        elif row[0] == "numlayers" and len(row) >= 2:
            data["numlayers"] = int(row[1])
        elif row[0] == "layer" and len(row) >= 4:
            lid = int(row[1])
            typ = int(row[3])
            data["layers"].append(
                {
                    "id": lid,
                    "number": lid + 1,
                    "name": row[2],
                    "type": typ,
                    "type_name": LAYER_TYPES.get(typ, str(typ)),
                }
            )
    try:
        out.unlink()
    except OSError:
        pass
    return data


def edit_layer(
    layer_id: int,
    type: int | None = None,
    name: str | None = None,
    draw_color: int = -2,
    flash_color: int = -2,
    status: int = 1,
    timeout: float = 60,
) -> dict:
    """Change a layer's type (tag), name, colours or on/off status.

    ``type`` uses CAM350 codes: 0 Top, 1 Internal, 2 NegPlane, 3 Bottom,
    4 Graphic, 6 Border, 7 SilkTop, 8 SilkBottom, 11 MaskTop, 12 MaskBottom,
    13 Temporary, 14 PosPlane, 16/17 RefDes, 18 Insulator, 19/20 Paste,
    21 NC Data.  Colours -2 keep their current value.
    """
    info = layers(timeout=timeout)
    if not info.get("ok"):
        return info
    layer = next((l for l in info["layers"] if l["id"] == layer_id), None)
    if layer is None:
        return {"ok": False, "error": f"layer {layer_id} 不存在", "layers": info["layers"]}
    typ = layer["type"] if type is None else int(type)
    nm = layer["name"] if name is None else name
    script = (
        f"edit_layer@ {int(layer_id)},{int(draw_color)},{int(flash_color)},"
        f"{int(typ)},{int(status)},{q(nm)}"
    )
    res = run_macro(script, timeout=timeout, tag="edit_layer")
    res["script"] = script
    if res.get("ok"):
        res["layer"] = {
            "id": layer_id,
            "name": nm,
            "type": typ,
            "type_name": LAYER_TYPES.get(typ, str(typ)),
            "status": status,
        }
    return res


def open_job(job: str) -> dict:
    """Open a .cam job by restarting the resident instance."""
    path = win_path(job, must_exist=True)
    with _LOCK:
        if is_running():
            stop(force=True, timeout=10)
        return start(job=path)


def save_job(path: str) -> dict:
    p = win_path(path)
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    return run_macro(f"setsaveasfile@ {q(p)}", tag="save")


def import_path(source: str, mode: str = "auto", timeout: float = 180) -> dict:
    """Import all recognised files from a folder (Gerber/Excellon/...)."""
    path = win_path(source, must_exist=True)
    src = Path(path)
    folder = str(src if src.is_dir() else src.parent)
    cmd = "auto_import@" if mode != "nc" else "Auto_Import_NCData@"
    script = "\n".join(
        [
            "setnewfile@" if mode == "new" else "' keep current database",
            f"{cmd} {q(folder)}",
            "update_layerbar@",
            "update_dcodebar@",
        ]
    )
    res = run_macro(script, timeout=timeout, tag="import")
    if res.get("ok"):
        res["layers"] = layers()
    return res


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------

def _fmt_params(kind: str, fmt: str, units: int, digits: tuple[int, int], zero: int) -> list[str]:
    """kind 0=photoplotter 1=drill 2=mill."""
    codes = {"274d": 0, "274x": 1, "excellon": 5}
    f = codes.get(fmt.lower())
    if f is None:
        raise ValueError(f"unsupported fmt: {fmt}")
    return [
        f"setup_fmtype@ {kind},{f}",
        f"setup_fmtunit@ {kind},{units}",
        f"setup_fmtdigits@ {kind},{digits[0]},{digits[1]}",
        f"setup_fmtmode@ {kind},0",
        f"setup_fmtzero@ {kind},{zero}",
    ]


def export_gerber(
    out_dir: str,
    layer_ids: list[int] | None = None,
    fmt: str = "274x",
    units: str | None = None,
    digits: tuple[int, int] = (3, 4),
    zero: str = "leading",
    mirror: bool = False,
    negative: bool = False,
    include_nc: bool = False,
    timeout: float = 300,
) -> dict:
    out = Path(win_path(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    info = layers()
    if not info.get("ok"):
        return info
    if layer_ids is None:
        layer_ids = [
            l["id"]
            for l in info["layers"]
            if (l["type"] in NC_TYPES) == include_nc and l["type"] != 21
        ]
        if include_nc:
            layer_ids = [l["id"] for l in info["layers"]]
    if not layer_ids:
        return {"ok": False, "error": "没有可导出的图层"}

    unit_code = 1 if (units or info.get("units_name")) == "metric" else 0
    zero_code = {"leading": 0, "trailing": 1, "none": 2}.get(zero, 0)
    lines = _fmt_params("0", fmt, unit_code, digits, zero_code)
    manifest = []
    for lid in layer_ids:
        layer = next((l for l in info["layers"] if l["id"] == lid), None)
        name = safe_name(Path(layer["name"] if layer else f"L{lid + 1}").stem)
        target = out / f"{lid + 1:02d}_{name}.gbr"
        lines.append(f"set_layermirrorstate@ {lid},{1 if mirror else 0}")
        lines.append(f"layer_setimagepolarity@ {lid},{1 if negative else 0}")
        lines.append(f"exportgbrfile@ {lid},-1,{q(target)}")
        manifest.append({"layer_id": lid, "layer": layer, "file": str(target)})
    res = run_macro("\n".join(lines), timeout=timeout, tag="export_gerber")
    res["files"] = _file_list([Path(m["file"]) for m in manifest])
    res["manifest"] = manifest
    res["settings"] = {
        "fmt": fmt,
        "units": "metric" if unit_code == 1 else "english",
        "digits": f"{digits[0]}.{digits[1]}",
        "zero": zero,
        "mirror": mirror,
        "negative": negative,
    }
    return res


def export_drill(
    out_dir: str,
    layer_ids: list[int] | None = None,
    units: str | None = None,
    digits: tuple[int, int] = (3, 3),
    zero: str = "leading",
    timeout: float = 300,
) -> dict:
    out = Path(win_path(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    info = layers()
    if not info.get("ok"):
        return info
    if layer_ids is None:
        layer_ids = [l["id"] for l in info["layers"] if l["type"] in NC_TYPES]
    if not layer_ids:
        return {"ok": False, "error": "数据库中没有 NC/钻孔图层"}
    unit_code = 1 if (units or info.get("units_name")) == "metric" else 0
    zero_code = {"leading": 0, "trailing": 1, "none": 2}.get(zero, 0)
    lines = _fmt_params("1", "excellon", unit_code, digits, zero_code)
    manifest = []
    for lid in layer_ids:
        layer = next((l for l in info["layers"] if l["id"] == lid), None)
        name = safe_name(Path(layer["name"] if layer else f"drill_L{lid + 1}").stem)
        target = out / f"{lid + 1:02d}_{name}.drl"
        lines.append(f"drill_out@ {lid},{q(target)}")
        manifest.append({"layer_id": lid, "layer": layer, "file": str(target)})
    res = run_macro("\n".join(lines), timeout=timeout, tag="export_drill")
    res["files"] = _file_list([Path(m["file"]) for m in manifest])
    res["manifest"] = manifest
    res["settings"] = {
        "units": "metric" if unit_code == 1 else "english",
        "digits": f"{digits[0]}.{digits[1]}",
        "zero": zero,
    }
    return res


def export_odb(out_path: str, timeout: float = 300) -> dict:
    """Export ODB++ .tgz.  ``out_path`` may be a .tgz file or a directory."""
    p = Path(win_path(out_path))
    if p.suffix.lower() == ".tgz":
        tgz = p
        base = p.with_suffix("")
    else:
        p.mkdir(parents=True, exist_ok=True)
        base = p / (p.name or "odb")
        tgz = base.with_suffix(".tgz")
    base.parent.mkdir(parents=True, exist_ok=True)
    script = "\n".join(
        [
            f"export_odb_initinfo@ {q(tgz)}",
            f"export_odb@ {q(base)}",
        ]
    )
    res = run_macro(script, timeout=timeout, tag="export_odb")
    res["files"] = _file_list([tgz])
    return res


# --------------------------------------------------------------------------
# panelization
# --------------------------------------------------------------------------

def panelize_script(
    panel_x: float,
    panel_y: float,
    border: tuple[float, float, float, float] | None = None,
    image_spacing: tuple[float, float] | None = None,
    rotation: int = 0,
    spread_layer: int = 11,
    outline: bool = False,
    outline_dcode: int = 10,
    outline_layer: int = 0,
    step_off: float = 0.0,
    center_images: bool = True,
) -> str:
    """Build the classic (Autofilm) panelization macro.

    This is the sequence documented in Utilities_Macros help and verified
    against CAM350 12.1.  All dimensions are in current database units.
    ``border`` is (left, top, right, bottom); the classic command only has
    X/Y values, so the larger horizontal/vertical values are used.
    """
    if rotation not in (0, 90, 180, 270):
        raise ValueError("rotation must be 0, 90, 180 or 270")
    bx = max(border[0], border[2]) if border else 0.0
    by = max(border[1], border[3]) if border else 0.0
    sx, sy = image_spacing if image_spacing else (0.0, 0.0)
    lines = [
        f"panel_spread@ 1,{int(spread_layer)}",
        f"panel_spacing@ {bx:.4f},{by:.4f},{sx:.4f},{sy:.4f},0",
        f"panel_dimension@ {panel_x:.4f},{panel_y:.4f}",
        f"panel_outline@ {1 if outline else 0},{int(outline_dcode)},{int(outline_layer)},{step_off:.4f}",
        f"panel_centerimg@ {1 if center_images else 0}",
        f"setfilmbox@ 0,{panel_x:.4f},{panel_y:.4f},0",
        "panelization@",
        "update_layerbar@",
    ]
    if rotation:
        lines.append(f"panel_rotate@ {rotation},1")
    return "\n".join(lines)


def panelize(
    panel_x: float,
    panel_y: float,
    border: tuple[float, float, float, float] | None = None,
    image_spacing: tuple[float, float] | None = None,
    rotation: int = 0,
    spread_layer: int = 11,
    outline: bool = False,
    outline_dcode: int = 10,
    outline_layer: int = 0,
    step_off: float = 0.0,
    save_as: str | None = None,
    timeout: float = 600,
) -> dict:
    script = panelize_script(
        panel_x, panel_y, border, image_spacing, rotation,
        spread_layer, outline, outline_dcode, outline_layer, step_off,
    )
    if save_as:
        path = win_path(save_as)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        script += "\nsetsaveasfile@ " + q(path)
    res = run_macro(script, timeout=timeout, tag="panelize")
    res["script"] = script
    if res.get("ok"):
        res["panel"] = {
            "dimensions": [panel_x, panel_y],
            "border": border,
            "image_spacing": image_spacing,
            "rotation": rotation,
            "outline": outline,
        }
        res["layers"] = layers()
        if save_as:
            res["saved"] = str(path)
    return res


# --------------------------------------------------------------------------
# help / introspection
# --------------------------------------------------------------------------

_REF_CACHE: dict | None = None


def reference() -> dict:
    global _REF_CACHE
    if _REF_CACHE is None:
        if not REFERENCE.exists():
            raise FileNotFoundError(f"Reference index not found: {REFERENCE}. {REFERENCE_HINT}")
        _REF_CACHE = json.loads(REFERENCE.read_text(encoding="utf-8"))
    return _REF_CACHE


def reference_info() -> dict:
    """Describe reference availability without raising."""
    info = {"available": REFERENCE.exists(), "path": str(REFERENCE)}
    if not info["available"]:
        info["hint"] = REFERENCE_HINT
        return info
    try:
        ref = reference()
    except Exception as exc:  # corrupt / unreadable JSON
        info.update(available=False, error=str(exc), hint=REFERENCE_HINT)
        return info
    info["commands"] = len(ref.get("commands", []))
    info["queries"] = len(ref.get("queries", []))
    return info


def search_reference(term: str = "", limit: int = 50) -> dict:
    """Search the macro/query reference extracted from CAM350 help."""
    ref = reference()
    needle = term.lower().strip()
    def hit(text: str) -> bool:
        return needle in text.lower()
    cmds = [
        c for c in ref["commands"]
        if not needle or hit(c.get("command", "")) or hit(c.get("description", "")) or hit(c.get("page", ""))
    ]
    qrs = [
        x for x in ref["queries"]
        if not needle or hit(x.get("query", "")) or hit(x.get("description", "")) or hit(x.get("page", ""))
    ]
    return {
        "term": term,
        "command_count": len(ref["commands"]),
        "query_count": len(ref["queries"]),
        "matched_commands": len(cmds),
        "matched_queries": len(qrs),
        "commands": cmds[:limit],
        "queries": qrs[:limit],
        "basic_keywords": ref.get("basic_keywords", []),
    }


def _file_list(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        if p.exists():
            item = {"file": str(p), "size": p.stat().st_size}
            if p.suffix.lower() in (".txt", ".tsv", ".log", ".drc", ".json", ".csv") and item["size"] < 200_000:
                item["preview"] = read_text(p)[:2000]
            out.append(item)
    return out


# --------------------------------------------------------------------------
# DRC
# --------------------------------------------------------------------------

def drc(
    clearance: float | None = None,
    error_file: str | None = None,
    layers_spec: str = "electrical",
    checks: list[str] | None = None,
    pass_file: str | None = None,
    timeout: float = 900,
) -> dict:
    """Run a standard DRC pass (or a saved pass file) and collect the report.

    clearance is in current database units (mil or mm).  Defaults to 6 mil
    when the database uses English units, 0.15 mm when metric.
    """
    info = layers()
    if not info.get("ok"):
        return info
    unit = info.get("units_name")
    if clearance is None:
        clearance = 0.15 if unit == "metric" else 6.0
    err = Path(win_path(error_file)) if error_file else (CALLS / f"errors_{uuid.uuid4().hex[:8]}.drc")
    err.parent.mkdir(parents=True, exist_ok=True)

    lines = ["util_drc@"]
    if pass_file:
        pf = win_path(pass_file, must_exist=True)
        lines += [f"drc_LoadPass@ {q(pf)}", "drc_Run_Pass@ 0", "end@"]
    else:
        enabled = set(checks or [
            "trk2trk", "trk2pad", "pad2pad", "outline", "mintrk", "minpad",
            "redundpad", "pad2masktop", "pad2maskbot", "drill2mask", "drill2pad",
            "drillwopad", "padwodrill", "drill2trk",
        ])
        lines += [
            "drc_Add_Pass@",
            'drc_Add_PassName@ "MCP_Pass"',
            'drc_SetCurrentPass@ "MCP_Pass"',
            "pid% = drc_CurrentPassID!",
            f"drc_seterrorfile@ {q(err)}",
            "drc_deleteall@",
            "drc_Add_Drc_Lyr_To_Pass@ pid%, -1, 32, 0",
            "drc_Set_SpChk_Drill_Lyr@ pid%, -1, 21, 0",
            "drc_Set_SpChk_Copper_Lyr@ pid%, -1, 32, 0",
            "drc_Set_Drill2Pad_Drill_Lyr@ pid%, -1, 21, 0",
            "drc_Set_Drill2Pad_Pad_Lyr@ pid%, -1, 32, 0",
            "drc_Set_Drill2Mask_Drill_Lyr@ pid%, -1, 21, 0",
            "drc_Set_Drill2Mask_Mask_Lyr@ pid%, -1, 11, 0",
            "drc_Set_Drill2Mask_Mask_Lyr@ pid%, -1, 12, 0",
        ]
        val = f"{float(clearance):.4f}"
        flag = lambda k: f"drc_Set_{k}_Test@ pid%, {1 if k.lower() in enabled else 0}"  # noqa: E731
        # clearance checks
        lines += [
            flag("Trk2Trk"), f"drc_Set_Trk2Trk_Val@ pid%, {val}", 'drc_Set_Trk2Trk_Dcode@ pid%, ""',
            flag("Trk2Pad"), f"drc_Set_Trk2Pad_Val@ pid%, {val}", 'drc_Set_Trk2Pad_Dcode@ pid%, ""',
            flag("Pad2Pad"), f"drc_Set_Pad2Pad_Val@ pid%, {val}", 'drc_Set_Pad2Pad_Dcode@ pid%, ""',
            flag("Outline"), f"drc_Set_Outline_Val@ pid%, {val}",
            flag("MinTrk"), f"drc_Set_MinTrk_Val@ pid%, {val}", 'drc_Set_MinTrk_Dcode@ pid%, ""',
            flag("MinPad"), f"drc_Set_MinPad_Val@ pid%, {val}", 'drc_Set_MinPad_Dcode@ pid%, ""',
            flag("RedundPad"),
            # annular ring
            'drc_Set_AnRingPad_Dcode@ pid%, ""',
            'drc_Set_AnRingDrill_TRef@ pid%, ""',
            flag("Pad2MaskTop"), f"drc_Set_Pad2MaskTop_Val@ pid%, {val}",
            flag("Pad2MaskBot"), f"drc_Set_Pad2MaskBot_Val@ pid%, {val}",
            flag("Drill2Mask"), f"drc_Set_Drill2Mask_Val@ pid%, {val}",
            flag("Drill2Pad"), f"drc_Set_Drill2Pad_Val@ pid%, {val}",
            # special
            flag("DrillWOPad"), flag("PadWODrill"),
            flag("Drill2Trk"), f"drc_Set_Drill2Trk_Val@ pid%, {val}",
            "axy@ dbminx!,dbminy!",
            "axy@ dbmaxx!,dbmaxy!",
            "drc_NetlistExtract@",
            "drc_Run_Pass@ pid%",
            "end@",
        ]
    # error count query and marker
    count_file = CALLS / f"drc_count_{uuid.uuid4().hex[:8]}.tsv"
    lines += [
        f"OPEN {q(count_file)} FOR OUTPUT AS #200",
        'PRINT #200, "errors", Number_drc_errors!',
        "CLOSE #200",
    ]
    res = run_macro("\n".join(lines), timeout=timeout, tag="drc")
    res["error_file"] = str(err)
    if count_file.exists():
        for row in _parse_tsv(count_file):
            if row[0] == "errors" and len(row) >= 2:
                res["error_count"] = int(float(row[1]))
        try:
            count_file.unlink()
        except OSError:
            pass
    if err.exists():
        text = read_text(err)
        res["report_preview"] = text[:4000]
        res["report_size"] = len(text)
    return res
