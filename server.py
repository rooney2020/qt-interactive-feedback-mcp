# Interactive Feedback MCP
# Developed by Fábio Ferreira (https://x.com/fabiomlferreira)
# Inspired by/related to dotcursorrules.com (https://dotcursorrules.com/)
# Enhanced by Pau Oliva (https://x.com/pof) with ideas from https://github.com/ttommyth/interactive-mcp
import os
import sys
import json
import base64
import tempfile
import asyncio
import uuid
import socket

from fastmcp import FastMCP, Context
from fastmcp.utilities.types import Image
from pydantic import Field

mcp = FastMCP("Interactive Feedback MCP")

POLL_INTERVAL = 0.5
HEARTBEAT_INTERVAL = 10
MAX_HEARTBEAT_FAILURES = 3
SOCKET_PATH = os.path.join("/tmp", "mcp_feedback_daemon.sock")
DAEMON_STARTUP_TIMEOUT = 10.0


async def _ensure_daemon_running():
    """Start the feedback daemon if not already running."""
    if _daemon_is_alive():
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    daemon_path = os.path.join(script_dir, "feedback_daemon.py")

    env = os.environ.copy()
    if sys.platform == "linux":
        env.setdefault("QT_IM_MODULE", "fcitx")
        env.setdefault("XMODIFIERS", "@im=fcitx")

    await asyncio.create_subprocess_exec(
        sys.executable, "-u", daemon_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )

    deadline = asyncio.get_event_loop().time() + DAEMON_STARTUP_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.2)
        if _daemon_is_alive():
            return

    raise RuntimeError("Failed to start feedback daemon within timeout")


def _daemon_is_alive() -> bool:
    """Check if the daemon is reachable via its socket."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(SOCKET_PATH)
        sock.close()
        return True
    except (socket.error, FileNotFoundError, OSError):
        return False


async def _send_to_daemon(
    message: str,
    predefined_options: list[str] | None = None,
    tab_title: str = "",
    ctx: Context | None = None,
) -> dict:
    """Send a feedback request to the daemon and wait for the response."""
    session_id = uuid.uuid4().hex[:12]
    request = {
        "session_id": session_id,
        "tab_title": tab_title or f"\u4f1a\u8bdd #{session_id[:6]}",
        "message": message,
        "predefined_options": predefined_options or [],
    }

    reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)
    writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()

    elapsed = 0.0
    last_heartbeat = 0.0
    heartbeat_failures = 0

    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=POLL_INTERVAL)
            if line:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return json.loads(line.decode("utf-8").strip())
        except asyncio.TimeoutError:
            elapsed += POLL_INTERVAL
            if ctx and (elapsed - last_heartbeat) >= HEARTBEAT_INTERVAL:
                last_heartbeat = elapsed
                try:
                    await ctx.report_progress(
                        progress=elapsed,
                        total=elapsed + 43200,
                    )
                    await ctx.info(f"Waiting for user feedback... ({elapsed:.0f}s)")
                    heartbeat_failures = 0
                except Exception:
                    heartbeat_failures += 1
                    if heartbeat_failures >= MAX_HEARTBEAT_FAILURES:
                        writer.close()
                        raise RuntimeError("Lost connection to MCP client")
        except (ConnectionResetError, BrokenPipeError):
            raise RuntimeError("Daemon connection lost")


async def _launch_feedback_standalone(
    summary: str,
    predefined_options: list[str] | None = None,
    ctx: Context | None = None,
    window_id: int = 1,
) -> dict:
    """Fallback: launch feedback_ui.py as a standalone subprocess (legacy mode)."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        output_file = tmp.name

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        feedback_ui_path = os.path.join(script_dir, "feedback_ui.py")

        args = [
            sys.executable, "-u", feedback_ui_path,
            "--prompt", summary,
            "--output-file", output_file,
            "--predefined-options", "|||".join(predefined_options) if predefined_options else "",
            "--window-id", str(window_id),
        ]
        env = os.environ.copy()
        if sys.platform == "linux":
            env.setdefault("QT_IM_MODULE", "fcitx")
            env.setdefault("XMODIFIERS", "@im=fcitx")
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=env,
        )

        try:
            wait_task = asyncio.ensure_future(process.wait())
            elapsed = 0.0
            last_heartbeat = 0.0
            heartbeat_failures = 0
            while not wait_task.done():
                await asyncio.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL
                if not wait_task.done() and ctx and (elapsed - last_heartbeat) >= HEARTBEAT_INTERVAL:
                    last_heartbeat = elapsed
                    try:
                        await ctx.report_progress(progress=elapsed, total=elapsed + 43200)
                        await ctx.info(f"Waiting for user feedback... ({elapsed}s)")
                        heartbeat_failures = 0
                    except Exception:
                        heartbeat_failures += 1
                        if heartbeat_failures >= MAX_HEARTBEAT_FAILURES:
                            if process.returncode is None:
                                process.terminate()
                                try:
                                    await asyncio.wait_for(process.wait(), timeout=5)
                                except asyncio.TimeoutError:
                                    process.kill()
                            break
            await wait_task
        except (asyncio.CancelledError, Exception):
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
            raise

        if process.returncode != 0:
            stderr_bytes = await process.stderr.read()
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            raise Exception(
                f"Feedback UI exited with code {process.returncode}"
                + (f": {stderr_text}" if stderr_text else "")
            )

        with open(output_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        os.unlink(output_file)
        return data
    except Exception as e:
        if os.path.exists(output_file):
            os.unlink(output_file)
        raise e


@mcp.tool()
async def interactive_feedback(
    message: str = Field(description="The specific question for the user"),
    predefined_options: list = Field(default=None, description="Predefined options for the user to choose from (optional)"),
    ctx: Context = None,
):
    """Request interactive feedback from the user. Supports text and screenshot responses."""
    predefined_options_list = predefined_options if isinstance(predefined_options, list) else None

    window_id = os.getpid()
    tab_title = f"\u4f1a\u8bdd #{window_id}"

    max_attempts = 2
    last_error = None

    for attempt in range(max_attempts):
        try:
            await _ensure_daemon_running()
            result = await _send_to_daemon(
                message, predefined_options_list, tab_title=tab_title, ctx=ctx
            )
            break
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                continue
            try:
                result = await _launch_feedback_standalone(
                    message, predefined_options_list, ctx, window_id=1
                )
                break
            except Exception as fallback_err:
                return {
                    "interactive_feedback": (
                        f"[Feedback UI failed: daemon={last_error}, standalone={fallback_err}. "
                        "Please use AskQuestion tool as fallback.]"
                    )
                }

    text = result.get("interactive_feedback", "")
    images_b64 = result.get("images", [])

    if not images_b64:
        return {"interactive_feedback": text}

    decoded_images: list[bytes] = [base64.b64decode(img) for img in images_b64]

    run_id = uuid.uuid4().hex[:8]
    image_paths = []
    for i, img_bytes in enumerate(decoded_images):
        temp_path = os.path.join(tempfile.gettempdir(), f"mcp_feedback_{run_id}_{i}.png")
        with open(temp_path, 'wb') as f:
            f.write(img_bytes)
        image_paths.append(temp_path)

    feedback_with_paths = text
    if image_paths:
        paths_str = "\n".join(image_paths)
        feedback_with_paths += f"\n\n[Screenshots saved to:\n{paths_str}]"

    contents: list = [feedback_with_paths]
    for img_bytes in decoded_images:
        contents.append(Image(data=img_bytes, format="png"))

    return contents

if __name__ == "__main__":
    mcp.run(transport="stdio", log_level="ERROR")
