#!/usr/bin/env python3
"""Set up the craft-llm LXD container for copilot development."""

import argparse
import json
import os
import subprocess
import sys
import time

CONTAINER = "craft-llm"
HOME = os.path.expanduser("~")
HOST_UID = os.getuid()
HOST_GID = os.getgid()
CONTAINER_USER = "ubuntu"
CONTAINER_UID = 1000
CONTAINER_GID = 1000

MOUNTS = [
    ("github",  f"{HOME}/.github",  f"/home/{CONTAINER_USER}/.github"),
    ("copilot", f"{HOME}/.copilot", f"/home/{CONTAINER_USER}/.copilot"),
    ("dev",     f"{HOME}/dev",      f"/home/{CONTAINER_USER}/dev"),
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def run(cmd, **kwargs):
    print(f"  $ {' '.join(str(a) for a in cmd)}")
    subprocess.run(cmd, check=True, **kwargs)


def run_capture(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def wait_for_container(timeout=90):
    print(f"  Waiting for {CONTAINER} to be ready...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(
            ["lxc", "exec", CONTAINER, "--", "true"],
            capture_output=True,
        )
        if r.returncode == 0:
            print(" ready.")
            return
        print(".", end="", flush=True)
        time.sleep(2)
    print()
    sys.exit(f"ERROR: {CONTAINER} did not become ready within {timeout}s.")


def container_exists():
    r = run_capture(["lxc", "list", CONTAINER, "--format=json"])
    if r.returncode != 0:
        return False
    return any(c["name"] == CONTAINER for c in json.loads(r.stdout))


# ── Setup steps ──────────────────────────────────────────────────────────────

def create_container():
    print(f"\n[1/5] Launching {CONTAINER} (ubuntu:24.04)...")
    run(["lxc", "launch", "ubuntu:24.04", CONTAINER])
    wait_for_container()
    # LXD creates /home/ubuntu owned by root; fix so ubuntu user can write there
    run(["lxc", "exec", CONTAINER, "--", "chown", "ubuntu:ubuntu", "/home/ubuntu"])


def configure_idmap():
    print(f"\n[2/5] Configuring UID/GID mapping "
          f"(host {HOST_UID}:{HOST_GID} → container {CONTAINER_UID}:{CONTAINER_GID})...")
    idmap = f"uid {HOST_UID} {CONTAINER_UID}\ngid {HOST_GID} {CONTAINER_GID}"
    run(["lxc", "config", "set", CONTAINER, "raw.idmap", idmap])
    run(["lxc", "restart", CONTAINER])
    wait_for_container()


def add_mounts():
    print(f"\n[3/5] Adding bind mounts...")
    for name, host_path, container_path in MOUNTS:
        os.makedirs(host_path, exist_ok=True)
        run([
            "lxc", "config", "device", "add", CONTAINER, name, "disk",
            f"source={host_path}",
            f"path={container_path}",
        ])
    run(["lxc", "restart", CONTAINER])
    wait_for_container()


def install_packages():
    print(f"\n[4/5] Installing packages...")
    run(["lxc", "exec", CONTAINER, "--", "apt-get", "update", "-q"])
    run(["lxc", "exec", CONTAINER, "--", "apt-get", "install", "-y", "build-essential"])
    print("  Installing gh CLI...")
    run([
        "lxc", "exec", CONTAINER, "--", "bash", "-c",
        "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg"
        " | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg &&"
        " chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg &&"
        " echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg]"
        " https://cli.github.com/packages stable main'"
        " | tee /etc/apt/sources.list.d/github-cli.list &&"
        " apt-get update -q && apt-get install -y gh",
    ])
    print("  Installing GitHub Copilot CLI...")
    run([
        "lxc", "exec", CONTAINER, "--",
        "bash", "-c", "curl -fsSL https://gh.io/copilot-install | bash",
    ])


def run_make_setup():
    print(f"\n[5/5] Running make setup in snapcraft (this may take a few minutes)...")
    run([
        "lxc", "exec", CONTAINER,
        f"--user={CONTAINER_UID}",
        f"--group={CONTAINER_GID}",
        "--env", "HOME=/home/ubuntu",
        "--",
        "bash", "-c", "cd /home/ubuntu/dev/craft/snapcraft && make setup",
    ])


# ── Verification tests ────────────────────────────────────────────────────────

def check(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        return False


def run_tests():
    print("\n── Verification tests ──────────────────────────────────────────")

    def t_running():
        data = json.loads(run_capture(["lxc", "list", CONTAINER, "--format=json"]).stdout)
        matches = [c for c in data if c["name"] == CONTAINER]
        assert matches and matches[0]["status"] == "Running", \
            f"status={matches[0]['status'] if matches else 'not found'}"

    def t_build_essential():
        subprocess.run(
            ["lxc", "exec", CONTAINER, "--", "dpkg", "-l", "build-essential"],
            capture_output=True, check=True,
        )

    def t_gh_installed():
        subprocess.run(
            ["lxc", "exec", CONTAINER, "--", "gh", "--version"],
            capture_output=True, check=True,
        )

    def t_copilot_installed():
        r = subprocess.run(
            ["lxc", "exec", CONTAINER,
             f"--user={CONTAINER_UID}", f"--group={CONTAINER_GID}",
             "--env", "HOME=/home/ubuntu",
             "--", "copilot", "--version"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"exit {r.returncode}: {r.stderr.strip()}"
        assert "copilot" in (r.stdout + r.stderr).lower(), \
            f"unexpected output: {r.stdout!r}"

    def t_dev_mount_read():
        r = subprocess.run(
            ["lxc", "exec", CONTAINER, "--", "ls", "/home/ubuntu/dev/craft"],
            capture_output=True, text=True, check=True,
        )
        assert "snapcraft" in r.stdout, f"snapcraft not listed: {r.stdout!r}"

    def t_dev_ownership():
        r = subprocess.run(
            ["lxc", "exec", CONTAINER, "--",
             "stat", "-c", "%U", "/home/ubuntu/dev/craft/snapcraft"],
            capture_output=True, text=True, check=True,
        )
        owner = r.stdout.strip()
        assert owner == "ubuntu", f"owner is {owner!r}, expected 'ubuntu'"

    def t_github_mount():
        subprocess.run(
            ["lxc", "exec", CONTAINER, "--", "ls", "/home/ubuntu/.github"],
            capture_output=True, check=True,
        )

    def t_copilot_mount():
        subprocess.run(
            ["lxc", "exec", CONTAINER, "--", "ls", "/home/ubuntu/.copilot"],
            capture_output=True, check=True,
        )

    def t_write_transparency():
        test_file = f"{HOME}/dev/.craft_llm_test_file"
        subprocess.run(
            ["lxc", "exec", CONTAINER,
             f"--user={CONTAINER_UID}", f"--group={CONTAINER_GID}",
             "--", "touch", "/home/ubuntu/dev/.craft_llm_test_file"],
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
            ["lxc", "exec", CONTAINER, "--",
             "ls", "/home/ubuntu/dev/craft/snapcraft/.venv"],
            capture_output=True, check=True,
        )

    tests = [
        ("Container running",               t_running),
        ("build-essential installed",       t_build_essential),
        ("gh installed",                    t_gh_installed),
        ("Copilot CLI installed",           t_copilot_installed),
        ("dev mount readable",              t_dev_mount_read),
        ("dev mount ownership transparent", t_dev_ownership),
        (".github mount works",             t_github_mount),
        (".copilot mount works",            t_copilot_mount),
        ("Write transparency",              t_write_transparency),
        ("make setup completed (.venv)",    t_venv_exists),
    ]

    results = [check(name, fn) for name, fn in tests]
    passed = sum(results)
    total = len(results)

    print()
    if all(results):
        print("=" * 60)
        print("craft-llm container is ready!")
        print("  Mounts: ~/.github, ~/.copilot, ~/dev  →  /home/ubuntu/{...}")
        print(f"  UID/GID mapping: transparent "
              f"(host {HOST_UID}:{HOST_GID} ↔ container ubuntu)")
        print("  Packages: build-essential, copilot CLI")
        print("  snapcraft make setup: complete")
        print(f"All {total} tests passed.")
        print("=" * 60)
    else:
        print(f"{passed}/{total} tests passed. See failures above.")
        sys.exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=f"Set up the {CONTAINER} LXD container for copilot development."
    )
    parser.add_argument(
        "--recreate", action="store_true",
        help="Delete and recreate the container if it already exists.",
    )
    args = parser.parse_args()

    if container_exists():
        if not args.recreate:
            sys.exit(
                f"ERROR: Container '{CONTAINER}' already exists. "
                "Pass --recreate to replace it."
            )
        print(f"--recreate: stopping and deleting existing {CONTAINER}...")
        subprocess.run(["lxc", "stop", "--force", CONTAINER], check=False)
        subprocess.run(["lxc", "delete", CONTAINER], check=True)

    create_container()
    configure_idmap()
    add_mounts()
    install_packages()
    run_make_setup()
    run_tests()


if __name__ == "__main__":
    main()
