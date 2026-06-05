import argparse
import subprocess
import sys


def run(cmd: list[str]) -> int:
    print(">", " ".join(cmd))
    return subprocess.call(cmd)


def main() -> int:
    p = argparse.ArgumentParser(description="Developer Experience commands")
    p.add_argument("action", choices=["smoke", "tests", "lint", "run-bot", "run-api"])
    args = p.parse_args()

    if args.action == "smoke":
        return run([sys.executable, "-m", "py_compile", "main.py", "api.py"])
    if args.action == "tests":
        return run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])
    if args.action == "lint":
        print("Use IDE lint integration or run your configured linter toolchain.")
        return 0
    if args.action == "run-bot":
        return run([sys.executable, "main.py"])
    if args.action == "run-api":
        return run([sys.executable, "api.py"])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
