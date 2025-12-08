#!/usr/bin/env python3
"""
Alembic Migration Helper Script
Erleichtert häufige Migration-Aufgaben
"""

import sys
import subprocess
from typing import List


def run_command(cmd: List[str]) -> int:
    """Run a command and return its exit code"""
    print(f"🔧 Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def create_migration(message: str, autogenerate: bool = True):
    """Create a new migration"""
    cmd = ["alembic", "revision"]
    if autogenerate:
        cmd.append("--autogenerate")
    cmd.extend(["-m", message])
    return run_command(cmd)


def upgrade(revision: str = "head"):
    """Upgrade to a revision"""
    return run_command(["alembic", "upgrade", revision])


def downgrade(revision: str = "-1"):
    """Downgrade to a revision"""
    return run_command(["alembic", "downgrade", revision])


def current():
    """Show current revision"""
    return run_command(["alembic", "current"])


def history(verbose: bool = False):
    """Show migration history"""
    cmd = ["alembic", "history"]
    if verbose:
        cmd.append("--verbose")
    return run_command(cmd)


def stamp(revision: str = "head"):
    """Stamp database with a revision"""
    return run_command(["alembic", "stamp", revision])


def show_help():
    """Show help message"""
    print("""
🗄️  Alembic Migration Helper

Usage: python migrate.py <command> [options]

Commands:
  create <message>     Create new migration (autogenerate)
  create-empty <msg>   Create empty migration
  upgrade [revision]   Upgrade to revision (default: head)
  downgrade [revision] Downgrade to revision (default: -1)
  current              Show current revision
  history              Show migration history
  history -v           Show verbose history
  stamp [revision]     Stamp database (default: head)
  reset                Reset database (downgrade to base)
  help                 Show this help

Examples:
  python migrate.py create "Add user avatar column"
  python migrate.py upgrade
  python migrate.py downgrade -1
  python migrate.py current
  python migrate.py reset
    """)


def main():
    if len(sys.argv) < 2:
        show_help()
        return 1

    command = sys.argv[1].lower()

    if command == "create":
        if len(sys.argv) < 3:
            print("❌ Error: Migration message required")
            print("Usage: python migrate.py create <message>")
            return 1
        message = " ".join(sys.argv[2:])
        return create_migration(message, autogenerate=True)

    elif command == "create-empty":
        if len(sys.argv) < 3:
            print("❌ Error: Migration message required")
            print("Usage: python migrate.py create-empty <message>")
            return 1
        message = " ".join(sys.argv[2:])
        return create_migration(message, autogenerate=False)

    elif command == "upgrade":
        revision = sys.argv[2] if len(sys.argv) > 2 else "head"
        return upgrade(revision)

    elif command == "downgrade":
        revision = sys.argv[2] if len(sys.argv) > 2 else "-1"
        return downgrade(revision)

    elif command == "current":
        return current()

    elif command == "history":
        verbose = "-v" in sys.argv or "--verbose" in sys.argv
        return history(verbose)

    elif command == "stamp":
        revision = sys.argv[2] if len(sys.argv) > 2 else "head"
        return stamp(revision)

    elif command == "reset":
        print("⚠️  Warning: This will rollback all migrations!")
        response = input("Are you sure? (yes/no): ")
        if response.lower() == "yes":
            return downgrade("base")
        else:
            print("Cancelled.")
            return 0

    elif command in ["help", "-h", "--help"]:
        show_help()
        return 0

    else:
        print(f"❌ Unknown command: {command}")
        show_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
