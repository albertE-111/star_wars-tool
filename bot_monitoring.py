from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

MAIN_BOT_SCRIPT = Path("telegram_bot.py")
MAIN_BOT_LOCK_PATH = Path(".telegram_bot.lock")
MAIN_BOT_HEARTBEAT_PATH = Path(".telegram_bot.heartbeat.json")
BOT_EVENT_LOG_PATH = Path("telegram_bot_events.jsonl")
MAIN_BOT_PROCESS_LOG_PATH = Path("telegram_bot_process.log")
SUPPORT_BOT_SCRIPT = Path("support_bot.py")
SUPPORT_BOT_LOCK_PATH = Path(".support_bot.lock")
SUPPORT_BOT_PROCESS_LOG_PATH = Path("support_bot_process.log")
LIVE_MONITORING_BOT_SCRIPT = Path("live_monitoring_bot.py")
LIVE_MONITORING_BOT_LOCK_PATH = Path(".live_monitoring_bot.lock")
LIVE_MONITORING_BOT_HEARTBEAT_PATH = Path(".live_monitoring_bot.heartbeat.json")
LIVE_MONITORING_BOT_PROCESS_LOG_PATH = Path("live_monitoring_bot_process.log")
HEARTBEAT_INTERVAL_SECONDS = 30


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json_file(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def write_heartbeat(status: str, details: dict[str, Any] | None = None) -> None:
    payload = {
        "pid": os.getpid(),
        "status": status,
        "updated_at": now_iso(),
        "details": details or {},
    }
    save_json_file(MAIN_BOT_HEARTBEAT_PATH, payload)


def read_heartbeat() -> dict[str, Any] | None:
    payload = load_json_file(MAIN_BOT_HEARTBEAT_PATH, None)
    if not isinstance(payload, dict):
        return None
    return payload


def write_live_monitoring_heartbeat(status: str, details: dict[str, Any] | None = None) -> None:
    payload = {
        "pid": os.getpid(),
        "status": status,
        "updated_at": now_iso(),
        "details": details or {},
    }
    save_json_file(LIVE_MONITORING_BOT_HEARTBEAT_PATH, payload)


def read_live_monitoring_heartbeat() -> dict[str, Any] | None:
    payload = load_json_file(LIVE_MONITORING_BOT_HEARTBEAT_PATH, None)
    if not isinstance(payload, dict):
        return None
    return payload


def read_main_bot_pid() -> int | None:
    return read_pid_from_lock(MAIN_BOT_LOCK_PATH)


def read_pid_from_lock(lock_path: Path) -> int | None:
    if not lock_path.exists():
        return None
    try:
        for line in lock_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("pid="):
                return int(line.partition("=")[2].strip())
    except Exception:
        return None
    return None


def safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def find_python_process_for_script(script_name: str) -> int | None:
    if os.name != "nt":
        return None
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    f"Where-Object {{ $_.Name -eq 'python.exe' -and $_.CommandLine -like '*{script_name}*' }} | "
                    "Select-Object -ExpandProperty ProcessId"
                ),
            ],
            capture_output=True,
            text=False,
            check=False,
            timeout=15,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    output = (completed.stdout or b"").decode("utf-8", errors="ignore").strip()
    if not output:
        return None
    candidates: list[int] = []
    for line in output.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        try:
            candidates.append(int(candidate))
        except ValueError:
            continue
    if not candidates:
        return None
    return max(candidates)


def is_process_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=False,
                check=False,
            )
        except Exception:
            return False
        if completed.returncode != 0:
            return False
        raw_output = completed.stdout or b""
        output = raw_output.decode("utf-8", errors="ignore").strip()
        if not output or output.startswith("INFO:"):
            return False
        return str(pid) in output
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def get_main_bot_status() -> dict[str, Any]:
    pid = read_main_bot_pid()
    lock_exists = MAIN_BOT_LOCK_PATH.exists()
    if pid is None and lock_exists:
        pid = find_python_process_for_script(MAIN_BOT_SCRIPT.name)
    running = is_process_running(pid)
    if pid and not running and lock_exists:
        safe_unlink(MAIN_BOT_LOCK_PATH)
        lock_exists = False
    heartbeat = read_heartbeat()
    heartbeat_age_seconds = None
    if heartbeat and heartbeat.get("updated_at"):
        try:
            heartbeat_time = datetime.fromisoformat(str(heartbeat["updated_at"]))
            heartbeat_age_seconds = max(0.0, (datetime.now() - heartbeat_time).total_seconds())
        except ValueError:
            heartbeat_age_seconds = None

    return {
        "pid": pid,
        "running": running,
        "heartbeat": heartbeat,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "lock_exists": lock_exists,
    }


def get_support_bot_status() -> dict[str, Any]:
    pid = read_pid_from_lock(SUPPORT_BOT_LOCK_PATH)
    lock_exists = SUPPORT_BOT_LOCK_PATH.exists()
    if pid is None and lock_exists:
        pid = find_python_process_for_script(SUPPORT_BOT_SCRIPT.name)
    running = is_process_running(pid)
    if pid and not running and lock_exists:
        safe_unlink(SUPPORT_BOT_LOCK_PATH)
        lock_exists = False
    return {
        "pid": pid,
        "running": running,
        "lock_exists": lock_exists,
    }


def get_live_monitoring_bot_status() -> dict[str, Any]:
    pid = read_pid_from_lock(LIVE_MONITORING_BOT_LOCK_PATH)
    lock_exists = LIVE_MONITORING_BOT_LOCK_PATH.exists()
    if pid is None and lock_exists:
        pid = find_python_process_for_script(LIVE_MONITORING_BOT_SCRIPT.name)
    running = is_process_running(pid)
    if pid and not running and lock_exists:
        safe_unlink(LIVE_MONITORING_BOT_LOCK_PATH)
        lock_exists = False
    heartbeat = read_live_monitoring_heartbeat()
    heartbeat_age_seconds = None
    if heartbeat and heartbeat.get("updated_at"):
        try:
            heartbeat_time = datetime.fromisoformat(str(heartbeat["updated_at"]))
            heartbeat_age_seconds = max(0.0, (datetime.now() - heartbeat_time).total_seconds())
        except ValueError:
            heartbeat_age_seconds = None

    return {
        "pid": pid,
        "running": running,
        "heartbeat": heartbeat,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "lock_exists": lock_exists,
    }


def append_event(source: str, level: str, message: str, details: dict[str, Any] | None = None) -> int:
    event_id = time.time_ns()
    payload = {
        "id": event_id,
        "timestamp": now_iso(),
        "source": source,
        "level": level.upper(),
        "message": message,
        "details": details or {},
    }
    with BOT_EVENT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    return event_id


def read_recent_events(limit: int = 20, min_level: str = "") -> list[dict[str, Any]]:
    if not BOT_EVENT_LOG_PATH.exists():
        return []

    normalized_level = min_level.upper().strip()
    events: list[dict[str, Any]] = []
    for line in BOT_EVENT_LOG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if normalized_level and str(payload.get("level", "")).upper() != normalized_level:
            continue
        events.append(payload)
    return events[-limit:]


def read_events_after(last_event_id: int, limit: int = 50) -> list[dict[str, Any]]:
    if not BOT_EVENT_LOG_PATH.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in BOT_EVENT_LOG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        try:
            event_id = int(payload.get("id", 0))
        except (TypeError, ValueError):
            continue
        if event_id > last_event_id:
            events.append(payload)
    return events[:limit]


class JsonlEventHandler(logging.Handler):
    def __init__(self, source: str):
        super().__init__(level=logging.ERROR)
        self.source = source

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            details: dict[str, Any] = {
                "logger": record.name,
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            }
            if record.exc_info:
                details["traceback"] = "".join(traceback.format_exception(*record.exc_info))
            append_event(self.source, record.levelname, message, details)
        except Exception:
            self.handleError(record)


def configure_event_logging(source: str) -> None:
    root_logger = logging.getLogger()
    marker = f"_jsonl_event_handler_{source}"
    if getattr(root_logger, marker, False):
        return

    handler = JsonlEventHandler(source)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)
    setattr(root_logger, marker, True)


def start_main_bot_process(python_executable: str | None = None) -> dict[str, Any]:
    status = get_main_bot_status()
    if status["running"]:
        return {"ok": False, "message": f"Haupt-Bot laeuft bereits mit PID {status['pid']}."}

    executable = python_executable or sys.executable
    log_handle = MAIN_BOT_PROCESS_LOG_PATH.open("ab")
    kwargs: dict[str, Any] = {
        "cwd": str(Path.cwd()),
        "stdout": log_handle,
        "stderr": log_handle,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    try:
        process = subprocess.Popen([executable, str(MAIN_BOT_SCRIPT)], **kwargs)
    finally:
        log_handle.close()
    time.sleep(2)
    if not is_process_running(process.pid):
        safe_unlink(MAIN_BOT_LOCK_PATH)
        return {
            "ok": False,
            "message": (
                f"Haupt-Bot konnte nicht stabil gestartet werden (PID {process.pid}). "
                f"Pruefe {MAIN_BOT_PROCESS_LOG_PATH.name}."
            ),
            "pid": process.pid,
        }
    return {
        "ok": True,
        "message": f"Haupt-Bot wurde gestartet (PID {process.pid}).",
        "pid": process.pid,
    }


def start_support_bot_process(python_executable: str | None = None) -> dict[str, Any]:
    status = get_support_bot_status()
    if status["running"]:
        return {"ok": False, "message": f"Support-Bot laeuft bereits mit PID {status['pid']}."}

    executable = python_executable or sys.executable
    log_handle = SUPPORT_BOT_PROCESS_LOG_PATH.open("ab")
    kwargs: dict[str, Any] = {
        "cwd": str(Path.cwd()),
        "stdout": log_handle,
        "stderr": log_handle,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    try:
        process = subprocess.Popen([executable, str(SUPPORT_BOT_SCRIPT)], **kwargs)
    finally:
        log_handle.close()
    time.sleep(2)
    if not is_process_running(process.pid):
        safe_unlink(SUPPORT_BOT_LOCK_PATH)
        return {
            "ok": False,
            "message": (
                f"Support-Bot konnte nicht stabil gestartet werden (PID {process.pid}). "
                f"Pruefe {SUPPORT_BOT_PROCESS_LOG_PATH.name}."
            ),
            "pid": process.pid,
        }
    return {
        "ok": True,
        "message": f"Support-Bot wurde gestartet (PID {process.pid}).",
        "pid": process.pid,
    }


def start_live_monitoring_bot_process(python_executable: str | None = None) -> dict[str, Any]:
    status = get_live_monitoring_bot_status()
    if status["running"]:
        return {"ok": False, "message": f"Live-Monitoring-Bot laeuft bereits mit PID {status['pid']}."}

    executable = python_executable or sys.executable
    log_handle = LIVE_MONITORING_BOT_PROCESS_LOG_PATH.open("ab")
    kwargs: dict[str, Any] = {
        "cwd": str(Path.cwd()),
        "stdout": log_handle,
        "stderr": log_handle,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    try:
        process = subprocess.Popen([executable, str(LIVE_MONITORING_BOT_SCRIPT)], **kwargs)
    finally:
        log_handle.close()
    time.sleep(2)
    if not is_process_running(process.pid):
        safe_unlink(LIVE_MONITORING_BOT_LOCK_PATH)
        return {
            "ok": False,
            "message": (
                f"Live-Monitoring-Bot konnte nicht stabil gestartet werden (PID {process.pid}). "
                f"Pruefe {LIVE_MONITORING_BOT_PROCESS_LOG_PATH.name}."
            ),
            "pid": process.pid,
        }
    return {
        "ok": True,
        "message": f"Live-Monitoring-Bot wurde gestartet (PID {process.pid}).",
        "pid": process.pid,
    }


def stop_main_bot_process() -> dict[str, Any]:
    status = get_main_bot_status()
    pid = status["pid"]
    if not status["running"]:
        safe_unlink(MAIN_BOT_LOCK_PATH)
        return {"ok": False, "message": "Haupt-Bot laeuft aktuell nicht."}

    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "taskkill fehlgeschlagen")
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        return {"ok": False, "message": f"Haupt-Bot konnte nicht gestoppt werden: {exc}"}

    time.sleep(1)
    safe_unlink(MAIN_BOT_LOCK_PATH)
    return {"ok": True, "message": f"Haupt-Bot wurde gestoppt (PID {pid}).", "pid": pid}


def stop_support_bot_process() -> dict[str, Any]:
    status = get_support_bot_status()
    pid = status["pid"]
    if not status["running"]:
        safe_unlink(SUPPORT_BOT_LOCK_PATH)
        return {"ok": False, "message": "Support-Bot laeuft aktuell nicht."}

    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "taskkill fehlgeschlagen")
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        return {"ok": False, "message": f"Support-Bot konnte nicht gestoppt werden: {exc}"}

    time.sleep(1)
    safe_unlink(SUPPORT_BOT_LOCK_PATH)
    return {"ok": True, "message": f"Support-Bot wurde gestoppt (PID {pid}).", "pid": pid}


def stop_live_monitoring_bot_process() -> dict[str, Any]:
    status = get_live_monitoring_bot_status()
    pid = status["pid"]
    if not status["running"]:
        safe_unlink(LIVE_MONITORING_BOT_LOCK_PATH)
        return {"ok": False, "message": "Live-Monitoring-Bot laeuft aktuell nicht."}

    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "taskkill fehlgeschlagen")
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        return {"ok": False, "message": f"Live-Monitoring-Bot konnte nicht gestoppt werden: {exc}"}

    time.sleep(1)
    safe_unlink(LIVE_MONITORING_BOT_LOCK_PATH)
    return {"ok": True, "message": f"Live-Monitoring-Bot wurde gestoppt (PID {pid}).", "pid": pid}


def restart_main_bot_process(python_executable: str | None = None) -> dict[str, Any]:
    stop_result = stop_main_bot_process()
    if stop_result["ok"]:
        time.sleep(2)
    start_result = start_main_bot_process(python_executable=python_executable)
    if start_result["ok"]:
        return {
            "ok": True,
            "message": (
                f"Restart ausgefuehrt. "
                f"{stop_result['message'] if stop_result else ''} {start_result['message']}"
            ).strip(),
            "pid": start_result.get("pid"),
        }
    if stop_result["ok"]:
        return {"ok": False, "message": f"Stop war erfolgreich, Start aber fehlgeschlagen: {start_result['message']}"}
    return {"ok": False, "message": f"Restart fehlgeschlagen: {stop_result['message']} | {start_result['message']}"}


def restart_support_bot_process(python_executable: str | None = None) -> dict[str, Any]:
    stop_result = stop_support_bot_process()
    if stop_result["ok"]:
        time.sleep(2)
    start_result = start_support_bot_process(python_executable=python_executable)
    if start_result["ok"]:
        return {
            "ok": True,
            "message": (
                f"Restart ausgefuehrt. "
                f"{stop_result['message'] if stop_result else ''} {start_result['message']}"
            ).strip(),
            "pid": start_result.get("pid"),
        }
    if stop_result["ok"]:
        return {"ok": False, "message": f"Stop war erfolgreich, Start aber fehlgeschlagen: {start_result['message']}"}
    return {"ok": False, "message": f"Restart fehlgeschlagen: {stop_result['message']} | {start_result['message']}"}


def restart_live_monitoring_bot_process(python_executable: str | None = None) -> dict[str, Any]:
    stop_result = stop_live_monitoring_bot_process()
    if stop_result["ok"]:
        time.sleep(2)
    start_result = start_live_monitoring_bot_process(python_executable=python_executable)
    if start_result["ok"]:
        return {
            "ok": True,
            "message": (
                f"Restart ausgefuehrt. "
                f"{stop_result['message'] if stop_result else ''} {start_result['message']}"
            ).strip(),
            "pid": start_result.get("pid"),
        }
    if stop_result["ok"]:
        return {"ok": False, "message": f"Stop war erfolgreich, Start aber fehlgeschlagen: {start_result['message']}"}
    return {"ok": False, "message": f"Restart fehlgeschlagen: {stop_result['message']} | {start_result['message']}"}


class SingleInstanceLock:
    def __init__(self, path: Path, already_running_message: str):
        self.path = path
        self.already_running_message = already_running_message
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                if self.handle.tell() == 0:
                    self.handle.write(b" ")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.release()
            raise RuntimeError(self.already_running_message) from exc

        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(
            (
                f"pid={os.getpid()}\n"
                f"executable={os.sys.executable}\n"
                f"started_at={datetime.now().isoformat(timespec='seconds')}\n"
            ).encode("utf-8")
        )
        self.handle.flush()

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
