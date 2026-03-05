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
import fcntl

from fastmcp import FastMCP, Context
from fastmcp.utilities.types import Image
from pydantic import Field

mcp = FastMCP("Interactive Feedback MCP")

POLL_INTERVAL = 0.5
HEARTBEAT_INTERVAL = 10
MAX_HEARTBEAT_FAILURES = 3
_LOCK_DIR = os.path.join(tempfile.gettempdir(), "mcp_feedback_windows")


def _acquire_window_id() -> tuple[int, object]:
    """Acquire a globally unique window ID using file locks across processes."""
    os.makedirs(_LOCK_DIR, exist_ok=True)
    window_id = 1
    while True:
        lock_path = os.path.join(_LOCK_DIR, f"window_{window_id}.lock")
        fd = open(lock_path, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd.write(str(os.getpid()))
            fd.flush()
            return window_id, fd
        except (IOError, OSError):
            fd.close()
            window_id += 1


def _release_window_id(fd):
    """Release a window ID lock by closing the file descriptor."""
    try:
        lock_path = fd.name
        fd.close()
        os.unlink(lock_path)
    except (OSError, AttributeError):
        pass


async def launch_feedback_ui(
    summary: str,
    predefined_options: list[str] | None = None,
    ctx: Context | None = None,
    window_id: int = 1,
) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        output_file = tmp.name

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        feedback_ui_path = os.path.join(script_dir, "feedback_ui.py")

        args = [
            sys.executable,
            "-u",
            feedback_ui_path,
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
                        await ctx.report_progress(
                            progress=elapsed,
                            total=elapsed + 43200,
                        )
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
    window_id, lock_fd = _acquire_window_id()

    predefined_options_list = predefined_options if isinstance(predefined_options, list) else None
    max_attempts = 2
    last_error = None
    for attempt in range(max_attempts):
        try:
            result = await launch_feedback_ui(message, predefined_options_list, ctx, window_id=window_id)
            break
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                continue
            _release_window_id(lock_fd)
            return {
                "interactive_feedback": (
                    f"[Feedback UI failed after {max_attempts} attempts: {last_error}. "
                    "Please use AskQuestion tool as fallback.]"
                )
            }

    _release_window_id(lock_fd)

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
