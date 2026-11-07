"""MCP server exposing DownStream CAM350 12.x automation.

Run on native Windows Python (normally through the ``cam350-mcp`` console
script installed by pip):

    cam350-mcp
    python -m cam350_mcp.server

All CAM350 access goes through the official macro language (``xxx@``
commands, ``xxx!`` queries).  A resident watcher instance is kept for fast,
stateful operations; batch mode starts a fresh ``camnt95.exe`` per call.
CAM350 allows only one automation session at a time, so calls are serialized.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

from . import __version__, core

logging.getLogger("cam350").info("MCP server starting")

mcp = MCPServer(
    name="cam350",
    title="CAM350",
    version=__version__,
    instructions=(
        "Control DownStream CAM350 12.1 through its native macro language. "
        "Typical flow: cam350_status -> cam350_start/open_job -> cam350_layers -> "
        "cam350_import/export/query/drc -> cam350_stop. "
        "Macros use commands ending in '@' and queries ending in '!' "
        "(e.g. 'numlayers!', 'Units!'). Use cam350_reference to look up commands. "
        "Windows paths (C:\\...) are required; /mnt/c/... is converted automatically. "
        "Only one CAM350 automation session can exist: batch calls need the "
        "resident instance stopped (or force=true)."
    ),
)


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------

@mcp.tool()
def cam350_status() -> dict:
    """Report CAM350 process/window state, job info and server configuration."""
    pid_list = core.pids()
    state = core.read_state()
    win = core.main_window(pid_list[0]) if pid_list else None
    all_windows = core.list_dialogs() if pid_list else []
    dialogs = [d for d in all_windows if not win or d["hwnd"] != win["hwnd"]]
    return {
        "running": bool(pid_list),
        "pids": pid_list,
        "main_window": win,
        "dialogs": dialogs,
        "state": state,
        "exe": str(core.EXE) if core.EXE else None,
        "exe_exists": bool(core.EXE and core.EXE.exists()),
        "reference": core.reference_info(),
        "data_dir": str(core.BASE),
        "runtime": str(core.RUNTIME),
    }


@mcp.tool()
def cam350_start(
    job: str | None = None,
    import_dir: str | None = None,
    save_as: str | None = None,
    timeout: float = 90,
) -> dict:
    """Start a resident CAM350 instance.

    job:       .cam database to open (``setopenfile@`` must run at top level,
               so opening a job always starts a new instance).
    import_dir: folder with Gerber/Excellon files to auto-import at boot.
    save_as:   optional .cam path to save the imported database to.
    A non-.cam ``job`` is treated as an import source (its folder is imported).
    """
    return core.start(job=job, import_dir=import_dir, save_as=save_as, timeout=timeout)


@mcp.tool()
def cam350_stop(timeout: float = 15, force: bool = False) -> dict:
    """Stop the resident instance gracefully (force=True kills it)."""
    return core.stop(timeout=timeout, force=force)


@mcp.tool()
def cam350_restart(job: str | None = None, import_dir: str | None = None, save_as: str | None = None) -> dict:
    """Stop the current instance and start a fresh one (optionally opening data)."""
    core.stop(timeout=10, force=True)
    return core.start(job=job, import_dir=import_dir, save_as=save_as)


@mcp.tool()
def cam350_abort() -> dict:
    """Interrupt a hung macro (clicks OK on a macro dialog) or kills the process."""
    return core.abort()


# --------------------------------------------------------------------------
# database operations
# --------------------------------------------------------------------------

@mcp.tool()
def cam350_open_job(job: str) -> dict:
    """Open a .cam database. Restarts the resident instance, then returns boot info."""
    return core.open_job(job)


@mcp.tool()
def cam350_save_job(path: str) -> dict:
    """Save the current database as ``path`` (setsaveasfile@)."""
    return core.save_job(path)


@mcp.tool()
def cam350_import(source: str, mode: str = "auto", timeout: float = 180) -> dict:
    """Import Gerber/Excellon/ODB files from a file or folder.

    mode: "auto"  = auto_import@ folder (keeps current database layers),
          "nc"    = Auto_Import_NCData@ (drill/rout files),
          "new"   = setnewfile@ first, then auto_import@.
    Returns updated layer list.
    """
    return core.import_path(source, mode=mode, timeout=timeout)


@mcp.tool()
def cam350_layers(timeout: float = 60) -> dict:
    """List layers: id (0-based), 1-based number, name, type code and type name."""
    return core.layers(timeout=timeout)


@mcp.tool()
def cam350_edit_layer(
    layer_id: int,
    type: int | None = None,
    name: str | None = None,
    draw_color: int = -2,
    flash_color: int = -2,
    status: int = 1,
    timeout: float = 60,
) -> dict:
    """Tag/change a layer: type, name, colours, on/off status.

    type codes: 0 Top, 1 Internal, 2 NegPlane, 3 Bottom, 4 Graphic,
    6 Border, 7 SilkTop, 8 SilkBottom, 11 MaskTop, 12 MaskBottom,
    13 Temporary, 14 PosPlane, 16/17 RefDes, 18 Insulator, 19/20 Paste,
    21 NC Data.  Colours -2 keep current values.  Use cam350_layers for ids.
    """
    return core.edit_layer(
        layer_id, type=type, name=name, draw_color=draw_color,
        flash_color=flash_color, status=status, timeout=timeout,
    )


@mcp.tool()
def cam350_query(expressions: list[str], timeout: float = 60) -> dict:
    """Evaluate CAM350 query variables.

    ``expressions`` must end with '!', e.g. ["numlayers!", "Units!",
    "dbminx!", "dbmaxx!", "Number_drc_errors!"].  Returns a dict of values.
    """
    return core.query(expressions, timeout=timeout)


# --------------------------------------------------------------------------
# macro execution
# --------------------------------------------------------------------------

@mcp.tool()
def cam350_run_macro(script: str, timeout: float = 180) -> dict:
    """Run a CAM350 macro in the resident instance (auto-starts a blank one).

    ``script`` is the macro body: one command per line, commands end with '@'
    (e.g. "setlayer@ 0" + newline + "delete_layer@").  A trailing setquit@/
    END/EXIT MACRO is removed so the instance stays alive.  Progress is
    written to the returned call_dir; unknown commands are reported as
    warnings (they can hang playback, so check them).
    """
    return core.run_macro(script, timeout=timeout)


@mcp.tool()
def cam350_batch_macro(script: str, timeout: float = 300, quit: bool = True, force: bool = False, tag: str = "batch") -> dict:
    """Run a macro in a fresh CAM350 process (``-M<macro>``), then quit.

    Use for one-shot jobs that must be isolated from the resident instance.
    CAM350 allows one automation session: if an instance is running this
    returns an error unless force=true (which stops it first).
    """
    return core.batch_macro(script, timeout=timeout, quit=quit, force=force, tag=tag)


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------

@mcp.tool()
def cam350_export_gerber(
    out_dir: str,
    layer_ids: list[int] | None = None,
    fmt: str = "274x",
    units: str | None = None,
    digits_x: int = 3,
    digits_y: int = 4,
    zero: str = "leading",
    mirror: bool = False,
    negative: bool = False,
    include_nc: bool = False,
    timeout: float = 300,
) -> dict:
    """Export copper/graphic layers as Gerber (fmt 274x|274d).

    layer_ids: 0-based layer ids; default = all non-NC layers.
    units: "metric"|"english" (default = database units).
    digits_x/digits_y: coordinate format, default 3.4.
    zero: leading|trailing|none.  Returns files with size + preview.
    """
    return core.export_gerber(
        out_dir,
        layer_ids=layer_ids,
        fmt=fmt,
        units=units,
        digits=(digits_x, digits_y),
        zero=zero,
        mirror=mirror,
        negative=negative,
        include_nc=include_nc,
        timeout=timeout,
    )


@mcp.tool()
def cam350_export_drill(
    out_dir: str,
    layer_ids: list[int] | None = None,
    units: str | None = None,
    digits_x: int = 3,
    digits_y: int = 3,
    zero: str = "leading",
    timeout: float = 300,
) -> dict:
    """Export NC/drill layers as Excellon files (default all type-21 layers)."""
    return core.export_drill(
        out_dir,
        layer_ids=layer_ids,
        units=units,
        digits=(digits_x, digits_y),
        zero=zero,
        timeout=timeout,
    )


@mcp.tool()
def cam350_export_odb(out_path: str, timeout: float = 300) -> dict:
    """Export the database as ODB++ (.tgz). out_path may be a .tgz file or a folder."""
    return core.export_odb(out_path, timeout=timeout)


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

@mcp.tool()
def cam350_drc(
    clearance: float | None = None,
    error_file: str | None = None,
    checks: list[str] | None = None,
    pass_file: str | None = None,
    timeout: float = 900,
) -> dict:
    """Run a DRC pass and return the error count + report preview.

    clearance: in database units (default 6 mil / 0.15 mm).
    checks: subset of ["trk2trk","trk2pad","pad2pad","outline","mintrk",
      "minpad","redundpad","pad2masktop","pad2maskbot","drill2mask",
      "drill2pad","drillwopad","padwodrill","drill2trk"].
    pass_file: optional saved DRC pass to load and run instead.
    error_file: where CAM350 writes the report (default: runtime/calls).
    """
    return core.drc(
        clearance=clearance,
        error_file=error_file,
        checks=checks,
        pass_file=pass_file,
        timeout=timeout,
    )


@mcp.tool()
def cam350_panelize(
    panel_x: float,
    panel_y: float,
    border_left: float | None = None,
    border_top: float | None = None,
    border_right: float | None = None,
    border_bottom: float | None = None,
    image_spacing_x: float | None = None,
    image_spacing_y: float | None = None,
    rotation: int = 0,
    outline: bool = False,
    outline_dcode: int = 10,
    outline_layer: int = 0,
    step_off: float = 0.0,
    save_as: str | None = None,
    timeout: float = 600,
) -> dict:
    """Panelize the current database (classic CAM350 Step & Repeat flow).

    Dimensions are in database units. border_* = panel border to image
    spacing on each side. image_spacing_* = minimum spacing between images.
    rotation = 0|90|180|270. outline=true draws a panel border on
    outline_layer using outline_dcode. The panel is generated on a new layer;
    optionally save the database. Returns the generated macro and layer list.
    """
    border = None
    if None not in (border_left, border_top, border_right, border_bottom):
        border = (border_left, border_top, border_right, border_bottom)
    spacing = None
    if image_spacing_x is not None and image_spacing_y is not None:
        spacing = (image_spacing_x, image_spacing_y)
    return core.panelize(
        panel_x,
        panel_y,
        border=border,
        image_spacing=spacing,
        rotation=rotation,
        outline=outline,
        outline_dcode=outline_dcode,
        outline_layer=outline_layer,
        step_off=step_off,
        save_as=save_as,
        timeout=timeout,
    )


# --------------------------------------------------------------------------
# GUI / diagnostics
# --------------------------------------------------------------------------

@mcp.tool()
def cam350_screenshot(name: str | None = None, focus: bool = True) -> Image:
    """Capture the CAM350 window area as a PNG image (brings it to the front)."""
    path = core.screenshot(name=name, focus=focus)
    return Image(path=str(path))


@mcp.tool()
def cam350_dialogs() -> dict:
    """List CAM350 dialogs (excluding the main window) and their buttons."""
    win = core.main_window()
    all_windows = core.list_dialogs()
    dialogs = [d for d in all_windows if not win or d["hwnd"] != win["hwnd"]]
    return {"dialogs": dialogs}


@mcp.tool()
def cam350_click_dialog(button: str = "OK", title: str | None = None) -> dict:
    """Click a button in a CAM350 dialog by caption (no focus needed)."""
    return core.click_dialog_button(button, title_substr=title)


@mcp.tool()
def cam350_reference(term: str = "", limit: int = 50) -> dict:
    """Search the optional CAM350 macro/query reference index.

    term matches command names, descriptions or help pages, e.g.
    "export", "panel", "drill", "Layer_Queries".  The index is not bundled
    with this project; set CAM350_MCP_REFERENCE to your own reference.json.
    """
    try:
        return core.search_reference(term, limit=limit)
    except FileNotFoundError as exc:
        return {"available": False, "error": str(exc)}


@mcp.tool()
def cam350_read_file(path: str, max_bytes: int = 200_000) -> dict:
    """Read a text file produced by CAM350 (DRC report, log, TSV...)."""
    p = Path(core.win_path(path, must_exist=True))
    data = p.read_bytes()[:max_bytes]
    return {
        "path": str(p),
        "size": p.stat().st_size,
        "truncated": p.stat().st_size > max_bytes,
        "text": data.decode("utf-8", errors="replace"),
    }


if __name__ == "__main__":
    mcp.run("stdio")
