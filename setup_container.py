#!/usr/bin/env python3
"""Set up the craft-llm LXD container for copilot development."""

import argparse
import json
import os
import subprocess
import sys
import time

CONTAINER_PREFIX = "craft-llm"
HOST_UID = os.getuid()
HOST_GID = os.getgid()
HOST_HOME = os.path.expanduser("~")
HOST_USER = os.path.basename(os.path.normpath(HOST_HOME))

# The container user is renamed to match the host, so paths are identical.
CONTAINER_USER = HOST_USER
CONTAINER_UID = 1000
CONTAINER_GID = 1000
CONTAINER_HOME = HOST_HOME

MOUNTS = [
    ("github", f"{HOST_HOME}/.github", f"{CONTAINER_HOME}/.github"),
    ("dev", f"{HOST_HOME}/dev", f"{CONTAINER_HOME}/dev"),
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def run(cmd, **kwargs):
    print(f"  $ {' '.join(str(a) for a in cmd)}")
    subprocess.run(cmd, check=True, **kwargs)


def run_capture(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def wait_for_container(container, timeout=90):
    print(f"  Waiting for {container} to be ready...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(
            ["lxc", "exec", container, "--", "true"],
            capture_output=True,
        )
        if r.returncode == 0:
            print(" ready.")
            break
        print(".", end="", flush=True)
        time.sleep(2)
    else:
        print()
        print(
            f"ERROR: {container} did not become ready within {timeout}s.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("  Waiting for cloud-init...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(
            ["lxc", "exec", container, "--", "cloud-init", "status"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and "done" in r.stdout:
            print(" done.")
            return
        print(".", end="", flush=True)
        time.sleep(2)
    print()
    print(f"ERROR: cloud-init did not finish within {timeout}s.", file=sys.stderr)
    sys.exit(1)


def container_exists(container):
    r = run_capture(["lxc", "list", container, "--format=json"])
    if r.returncode != 0:
        return False
    return any(c["name"] == container for c in json.loads(r.stdout))


# ── Setup steps ──────────────────────────────────────────────────────────────


def create_container(container):
    print(f"\n[1/5] Launching {container} (ubuntu:24.04)...")
    run(["lxc", "launch", "ubuntu:24.04", container])
    wait_for_container(container)
    # Rename the default ubuntu user/group to match the host user, and move the
    # home directory to the same path as on the host.  This ensures venv scripts
    # (whose shebangs reference HOST_HOME) resolve correctly in both environments
    # without any symlinks or re-syncing.
    run(
        [
            "lxc", "exec", container, "--",
            "usermod",
            "--badname",
            "--login", CONTAINER_USER,
            "--home", CONTAINER_HOME,
            "--move-home",
            "ubuntu",
        ]
    )
    run(
        [
            "lxc", "exec", container, "--",
            "groupmod", "--new-name", CONTAINER_USER, "ubuntu",
        ]
    )


def configure_idmap(container):
    print(
        f"\n[2/5] Configuring UID/GID mapping "
        f"(host {HOST_UID}:{HOST_GID} → container {CONTAINER_UID}:{CONTAINER_GID})..."
    )
    idmap = f"uid {HOST_UID} {CONTAINER_UID}\ngid {HOST_GID} {CONTAINER_GID}"
    run(["lxc", "config", "set", container, "raw.idmap", idmap])
    run(["lxc", "restart", container])
    wait_for_container(container)


def add_mounts(container):
    print("\n[3/5] Adding bind mounts...")
    for name, host_path, container_path in MOUNTS:
        os.makedirs(host_path, exist_ok=True)
        run(
            [
                "lxc",
                "config",
                "device",
                "add",
                container,
                name,
                "disk",
                f"source={host_path}",
                f"path={container_path}",
            ]
        )
    run(["lxc", "restart", container])
    wait_for_container(container)


def install_packages(container):
    print("\n[4/5] Installing packages...")
    run(["lxc", "exec", container, "--", "apt-get", "update", "-q"])
    run(["lxc", "exec", container, "--", "apt-get", "install", "-y", "build-essential"])

    print("  Installing gh CLI...")
    gh_setup = (
        "set -euo pipefail\n"
        "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg"
        " -o /usr/share/keyrings/githubcli-archive-keyring.gpg\n"
        "chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg\n"
        "echo 'deb"
        " [arch=amd64 signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg]"
        " https://cli.github.com/packages stable main'"
        " | tee /etc/apt/sources.list.d/github-cli.list\n"
        "apt-get update -q\n"
        "apt-get install -y gh"
    )
    run(["lxc", "exec", container, "--", "bash", "-c", gh_setup])


    print("  Configuring passwordless sudo...")
    run(
        [
            "lxc", "exec", container, "--",
            "bash", "-c",
            f"echo '{CONTAINER_USER} ALL=(ALL) NOPASSWD:ALL'"
            f" > /etc/sudoers.d/{CONTAINER_USER}"
            f" && chmod 440 /etc/sudoers.d/{CONTAINER_USER}",
        ]
    )


def run_make_setup():
    """Run ``make setup`` on the host.

    Because the container user has been renamed to match the host user (same
    username, same home path), the venv scripts produced here have shebangs that
    resolve correctly in both environments without any extra steps.
    """
    snapcraft_dir = os.path.join(HOST_HOME, "dev", "craft", "snapcraft")
    if not os.path.isdir(snapcraft_dir):
        print(f"ERROR: snapcraft directory not found: {snapcraft_dir}", file=sys.stderr)
        sys.exit(1)
    print(f"\n[5/5] Running make setup in snapcraft ({snapcraft_dir})...")
    run(["make", "setup"], cwd=snapcraft_dir)


# ── Verification tests ────────────────────────────────────────────────────────


def check(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        return False


def run_tests(container):
    print("\n── Verification tests ──────────────────────────────────────────")

    def t_running():
        data = json.loads(
            run_capture(["lxc", "list", container, "--format=json"]).stdout
        )
        matches = [c for c in data if c["name"] == container]
        assert matches and matches[0]["status"] == "Running", (
            f"status={matches[0]['status'] if matches else 'not found'}"
        )

    def t_build_essential():
        subprocess.run(
            ["lxc", "exec", container, "--", "dpkg", "-l", "build-essential"],
            capture_output=True,
            check=True,
        )

    def t_gh_installed():
        subprocess.run(
            ["lxc", "exec", container, "--", "gh", "--version"],
            capture_output=True,
            check=True,
        )

    def t_dev_mount_read():
        r = subprocess.run(
            ["lxc", "exec", container, "--", "ls", f"{CONTAINER_HOME}/dev/craft"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "snapcraft" in r.stdout, f"snapcraft not listed: {r.stdout!r}"

    def t_dev_ownership():
        r = subprocess.run(
            [
                "lxc",
                "exec",
                container,
                "--",
                "stat",
                "-c",
                "%U",
                f"{CONTAINER_HOME}/dev/craft/snapcraft",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        owner = r.stdout.strip()
        assert owner == CONTAINER_USER, (
            f"owner is {owner!r}, expected {CONTAINER_USER!r}"
        )

    def t_github_mount():
        subprocess.run(
            ["lxc", "exec", container, "--", "ls", f"{CONTAINER_HOME}/.github"],
            capture_output=True,
            check=True,
        )

    def t_write_transparency():
        test_file = f"{HOST_HOME}/dev/.{container}_test_file"
        subprocess.run(
            [
                "lxc",
                "exec",
                container,
                f"--user={CONTAINER_UID}",
                f"--group={CONTAINER_GID}",
                "--",
                "touch",
                f"{CONTAINER_HOME}/dev/.{container}_test_file",
            ],
            check=True,
        )
        try:
            st = os.stat(test_file)
            assert st.st_uid == HOST_UID, f"uid={st.st_uid}, expected {HOST_UID}"
            assert st.st_gid == HOST_GID, f"gid={st.st_gid}, expected {HOST_GID}"
        finally:
            if os.path.exists(test_file):
                os.unlink(test_file)

    def t_venv_exists():
        subprocess.run(
            [
                "lxc",
                "exec",
                container,
                "--",
                "ls",
                f"{CONTAINER_HOME}/dev/craft/snapcraft/.venv",
            ],
            capture_output=True,
            check=True,
        )

    def t_container_user():
        r = subprocess.run(
            ["lxc", "exec", container, "--", "id", "-un", f"{CONTAINER_UID}"],
            capture_output=True,
            text=True,
            check=True,
        )
        name = r.stdout.strip()
        assert name == CONTAINER_USER, (
            f"uid {CONTAINER_UID} maps to {name!r}, expected {CONTAINER_USER!r}"
        )

    def t_venv_interpreter_valid():
        """Venv Python interpreter must be executable on the host."""
        python = os.path.join(
            HOST_HOME, "dev", "craft", "snapcraft", ".venv", "bin", "python3"
        )
        assert os.path.exists(python), f"not found: {python}"
        r = subprocess.run([python, "--version"], capture_output=True, text=True)
        assert r.returncode == 0, f"exit {r.returncode}: {r.stderr.strip()}"

    tests = [
        ("Container running", t_running),
        ("build-essential installed", t_build_essential),
        ("gh installed", t_gh_installed),
        ("dev mount readable", t_dev_mount_read),
        ("dev mount ownership transparent", t_dev_ownership),
        (".github mount works", t_github_mount),
        ("Write transparency", t_write_transparency),
        ("make setup completed (.venv)", t_venv_exists),
        (f"container user is {CONTAINER_USER!r}", t_container_user),
        ("venv Python interpreter valid on host", t_venv_interpreter_valid),
    ]

    results = [check(name, fn) for name, fn in tests]
    passed = sum(results)
    total = len(results)

    print()
    if all(results):
        print("=" * 60)
        print("craft-llm container is ready!")
        print(f"  Mounts: ~/.github, ~/dev  →  {CONTAINER_HOME}/{{...}}")
        print(
            f"  UID/GID mapping: transparent "
            f"(host {HOST_UID}:{HOST_GID} ↔ container {CONTAINER_USER})"
        )
        print(f"  Container user: {CONTAINER_USER}")
        print("  Packages: build-essential, gh")
        print("  sudo: passwordless for container user")
        print("  Next: run 'gh auth login', 'gh copilot', and '/allow-all'")
        print(" PAT token perms: all repos, actions, issues, merge queues, metadata, pull requests")
        print("            user: copilot, gists")
        print("  snapcraft make setup: complete")
        print(f"All {total} tests passed.")
        print("=" * 60)
    else:
        print(f"{passed}/{total} tests passed. See failures above.")
        sys.exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Set up an LXD container for copilot development.",
        epilog=(
            "Example:\n"
            "  %(prog)s 1              # create craft-llm-1\n"
            "  %(prog)s 2 --recreate   # delete and recreate craft-llm-2"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "number",
        type=int,
        help="Container number suffix — creates a container named craft-llm-<number>.",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete the container if it already exists, then recreate it.",
    )
    args = parser.parse_args()

    container = f"{CONTAINER_PREFIX}-{args.number}"

    if container_exists(container):
        if not args.recreate:
            print(
                f"ERROR: container '{container}' already exists. "
                "Pass --recreate to delete and recreate it.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Deleting existing container: {container}")
        run(["lxc", "delete", "--force", container])

    print(f"Creating container: {container}")

    create_container(container)
    configure_idmap(container)
    add_mounts(container)
    install_packages(container)
    run_make_setup()
    run_tests(container)


if __name__ == "__main__":
    main()
