#!/usr/bin/env python3
"""Run the unittest suites of every project in this repository.

A project is a top-level, non-hidden subfolder that contains a ``tests/``
directory. Each one is run as::

    <this interpreter> -m unittest discover -s tests

with the project folder as the working directory.

Usage:
    python run_all_tests.py [--only a,b] [--skip a,b] [--fail-fast]

Exit status is 0 when every selected project passed, 1 otherwise.
Standard library only.
"""

import argparse
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
TIMEOUT_SECONDS = 900
TAIL_LINES = 30
RAN_RE = re.compile(r"^Ran (\d+) tests?\b", re.MULTILINE)


def find_projects(root):
    """Return sorted names of top-level folders that contain a tests/ directory."""
    names = []
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if not entry.is_dir():
                continue
            if os.path.isdir(os.path.join(entry.path, "tests")):
                names.append(entry.name)
    return sorted(names, key=str.lower)


def parse_names(text):
    return [part.strip() for part in text.split(",") if part.strip()]


def decode(data):
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def run_project(name):
    """Run one project's test suite and return its result row."""
    project_dir = os.path.join(ROOT, name)
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            cwd=project_dir,
            env=env,
            timeout=TIMEOUT_SECONDS,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = decode(proc.stdout)
        returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        output = decode(exc.stdout)
        returncode = None
    seconds = time.monotonic() - start

    counts = [int(n) for n in RAN_RE.findall(output)]
    if timed_out:
        result = "TIMEOUT"
    elif returncode == 0:
        result = "PASS"
    else:
        result = "FAIL"

    return {
        "name": name,
        "result": result,
        "tests": sum(counts) if counts else None,
        "seconds": seconds,
        "output": output,
    }


def skipped_row(name):
    return {"name": name, "result": "SKIP", "tests": None, "seconds": None, "output": ""}


def not_run_row(name):
    return {"name": name, "result": "NOT RUN", "tests": None, "seconds": None, "output": ""}


def print_table(rows):
    headers = ["Project", "Result", "Tests", "Seconds"]
    body = []
    for row in rows:
        tests = "-" if row["tests"] is None else str(row["tests"])
        seconds = "-" if row["seconds"] is None else "%.1f" % row["seconds"]
        body.append([row["name"], row["result"], tests, seconds])

    widths = [len(h) for h in headers]
    for line in body:
        for i, cell in enumerate(line):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells):
        # Project and Result are left-aligned; Tests and Seconds are right-aligned.
        out = []
        for i, cell in enumerate(cells):
            if i in (2, 3):
                out.append(cell.rjust(widths[i]))
            else:
                out.append(cell.ljust(widths[i]))
        return "  ".join(out).rstrip()

    rule = "  ".join("-" * w for w in widths)
    print(fmt(headers))
    print(rule)
    for line in body:
        print(fmt(line))


def print_failure_tails(rows):
    failed = [r for r in rows if r["result"] in ("FAIL", "TIMEOUT")]
    for row in failed:
        lines = row["output"].rstrip().splitlines()
        tail = lines[-TAIL_LINES:]
        print()
        print("--- %s: last %d line(s) of output ---" % (row["name"], len(tail)))
        for line in tail:
            print(line)


def ensure_lenient_streams():
    # Project output may contain non-ASCII text that the console encoding
    # cannot represent (e.g. cp949 on Windows). Replace instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the unittest suites of every project that has a tests/ folder."
    )
    parser.add_argument("--only", default="", metavar="A,B",
                        help="run only these projects (comma-separated folder names)")
    parser.add_argument("--skip", default="", metavar="A,B",
                        help="skip these projects (comma-separated folder names)")
    parser.add_argument("--fail-fast", action="store_true",
                        help="stop after the first failing project")
    args = parser.parse_args(argv)

    ensure_lenient_streams()

    all_projects = find_projects(ROOT)
    only = parse_names(args.only)
    skip = parse_names(args.skip)

    unknown_only = [n for n in only if n not in all_projects]
    if unknown_only:
        parser.error("unknown project(s) in --only: " + ", ".join(unknown_only))
    for name in skip:
        if name not in all_projects:
            print("warning: --skip names '%s', which is not a test project here; ignored"
                  % name, file=sys.stderr)

    candidates = [p for p in all_projects if not only or p in only]
    to_run = [p for p in candidates if p not in skip]
    if not to_run:
        print("no projects selected to run", file=sys.stderr)
        return 1

    rows = []
    stopped = False
    total = len(to_run)
    done = 0
    for name in candidates:
        if name in skip:
            rows.append(skipped_row(name))
            continue
        if stopped:
            rows.append(not_run_row(name))
            continue
        done += 1
        print("[%d/%d] %s ..." % (done, total, name), flush=True)
        row = run_project(name)
        print("      %s (%s test(s), %.1fs)" % (
            row["result"],
            "-" if row["tests"] is None else row["tests"],
            row["seconds"]), flush=True)
        rows.append(row)
        if args.fail_fast and row["result"] in ("FAIL", "TIMEOUT"):
            stopped = True

    print()
    print_table(rows)

    ran = [r for r in rows if r["seconds"] is not None]
    passed = sum(1 for r in rows if r["result"] == "PASS")
    failed = sum(1 for r in rows if r["result"] in ("FAIL", "TIMEOUT"))
    skipped = sum(1 for r in rows if r["result"] == "SKIP")
    not_run = sum(1 for r in rows if r["result"] == "NOT RUN")
    tests = sum(r["tests"] or 0 for r in ran)
    seconds = sum(r["seconds"] for r in ran)
    print()
    print("Projects: %d passed, %d failed, %d skipped, %d not run | tests: %d | time: %.1fs"
          % (passed, failed, skipped, not_run, tests, seconds))

    print_failure_tails(rows)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
