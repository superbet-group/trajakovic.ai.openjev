"""Start a command as a detached daemon (own session) and record its PID.

Usage: spawn.py --pidfile P --log L [--cwd D] -- CMD ARGS...

The new session keeps Ctrl+C in the calling terminal away from the daemon.
"""
import argparse
import datetime
import os
import shlex
import subprocess
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pidfile", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        print("spawn.py: no command given", file=sys.stderr)
        return 2

    log = open(a.log, "ab")
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    log.write(f"\n=== {stamp} start: {shlex.join(cmd)} ===\n".encode())
    log.flush()

    p = subprocess.Popen(
        cmd,
        cwd=a.cwd,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    tmp = a.pidfile + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"{p.pid}\n")
    os.replace(tmp, a.pidfile)

    time.sleep(1.0)
    code = p.poll()
    if code is not None:
        try:
            os.remove(a.pidfile)
        except OSError:
            pass
        print(f"exited immediately with code {code}; see {a.log}", file=sys.stderr)
        return 1
    print(p.pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
