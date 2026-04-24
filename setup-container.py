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
    # ("github", f"{HOST_HOME}/.github", f"{CONTAINER_HOME}/.github"),
    ("dev", f"{HOST_HOME}/dev", f"{CONTAINER_HOME}/dev"),
]

MAKE_SETUP_DIRS = [
    os.path.join(HOST_HOME, "dev", "craft", "snapcraft", "snapcraft-a"),
    os.path.join(HOST_HOME, "dev", "craft", "snapcraft", "snapcraft-b"),
    os.path.join(HOST_HOME, "dev", "craft", "snapcraft", "snapcraft-main"),
    os.path.join(HOST_HOME, "dev", "craft", "craft-parts"),
    os.path.join(HOST_HOME, "dev", "craft", "craft-providers"),
    os.path.join(HOST_HOME, "dev", "craft", "craft-application"),
    os.path.join(HOST_HOME, "dev", "craft", "craft-cli"),
    os.path.join(HOST_HOME, "dev", "craft", "craft-grammar"),
]

LSP_CONFIG_PATH = f"{CONTAINER_HOME}/.copilot/lsp-config.json"

PYLSP_LSP_CONFIG = {
    "lspServers": {
        "python": {
            "command": "pylsp",
            "args": [],
            "fileExtensions": {
                ".py": "python",
            },
        }
    }
}


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
            "lxc",
            "exec",
            container,
            "--",
            "usermod",
            "--badname",
            "--login",
            CONTAINER_USER,
            "--home",
            CONTAINER_HOME,
            "--move-home",
            "ubuntu",
        ]
    )
    run(
        [
            "lxc",
            "exec",
            container,
            "--",
            "groupmod",
            "--new-name",
            CONTAINER_USER,
            "ubuntu",
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
    # sudoers.d ignores files containing '.' - use a safe filename.
    # Use User_Alias with #uid to avoid issues with '@' in the username.
    run(
        [
            "lxc",
            "exec",
            container,
            "--",
            "bash",
            "-c",
            f"printf 'User_Alias CONTAINERUSER = #{CONTAINER_UID}\\nCONTAINERUSER ALL=(ALL) NOPASSWD:ALL\\n'"
            f" > /etc/sudoers.d/nopasswd-user"
            f" && chmod 440 /etc/sudoers.d/nopasswd-user",
        ]
    )

    print("  Installing astral-uv...")
    run(["lxc", "exec", container, "--", "snap", "install", "astral-uv", "--classic"])


def run_make_setup(container):
    """Run ``make setup`` in each craft project directory inside the container.

    The directories live under ~/dev which is bind-mounted, so the resulting
    venvs are visible on the host at the same paths.  ``make setup`` may be
    interactive (it installs apt packages via sudo), so stdin is inherited from
    the calling terminal.
    """
    print(f"\nRunning make setup in craft directories (in container)...")
    for directory in MAKE_SETUP_DIRS:
        if not os.path.isdir(directory):
            print(f"  WARNING: directory not found on host, skipping: {directory}")
            continue
        print(f"  Running make setup in {directory}...")
        run(
            [
                "lxc", "exec", container,
                f"--user={CONTAINER_UID}",
                f"--group={CONTAINER_GID}",
                f"--env=HOME={CONTAINER_HOME}",
                "--",
                "make", "-C", directory, "setup",
            ],
        )


def install_pylsp(container):
    """Install python-lsp-server via uv tool inside the container, ensure it is
    on PATH, and write the gh copilot LSP config into the container."""
    print("\n[5/5] Installing pylsp (python-lsp-server) in container...")

    def cexec(*cmd):
        return [
            "lxc", "exec", container,
            f"--user={CONTAINER_UID}",
            f"--group={CONTAINER_GID}",
            f"--env=HOME={CONTAINER_HOME}",
            "--", *cmd,
        ]

    run(cexec("uv", "tool", "install", "python-lsp-server"))
    # uv tool update-shell can't detect the shell via lxc exec, so append directly.
    run(cexec(
        "bash", "-c",
        r'grep -qxF "export PATH=$HOME/.local/bin:$PATH" ~/.bashrc'
        r' || echo "export PATH=$HOME/.local/bin:$PATH" >> ~/.bashrc',
    ))

    print(f"  Writing LSP config to {CONTAINER_HOME}/.copilot/lsp-config.json in container...")
    run(cexec("mkdir", "-p", f"{CONTAINER_HOME}/.copilot"))

    # Read any existing config from the container, then merge and write back.
    r = subprocess.run(
        cexec("cat", f"{CONTAINER_HOME}/.copilot/lsp-config.json"),
        capture_output=True,
        text=True,
    )
    existing = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    existing.setdefault("lspServers", {}).update(PYLSP_LSP_CONFIG["lspServers"])
    config_json = json.dumps(existing, indent=2) + "\n"

    subprocess.run(
        cexec("bash", "-c", f"cat > {CONTAINER_HOME}/.copilot/lsp-config.json"),
        input=config_json.encode(),
        check=True,
    )


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

    def t_passwordless_sudo():
        subprocess.run(
            [
                "lxc",
                "exec",
                container,
                f"--user={CONTAINER_UID}",
                "--",
                "sudo",
                "-n",
                "true",
            ],
            capture_output=True,
            check=True,
        )

    def t_uv_installed():
        subprocess.run(
            ["lxc", "exec", container, "--", "uv", "--version"],
            capture_output=True,
            check=True,
        )

    def t_venv_exists():
        missing = []
        for directory in MAKE_SETUP_DIRS:
            # Only check directories that exist on the host
            if not os.path.isdir(directory):
                continue
            venv = os.path.join(directory, ".venv")
            r = subprocess.run(
                ["lxc", "exec", container, "--", "ls", venv],
                capture_output=True,
            )
            if r.returncode != 0:
                missing.append(directory)
        assert not missing, f"missing .venv in: {missing}"

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
        """Venv Python interpreter must be executable on the host in all setup dirs."""
        failures = []
        for directory in MAKE_SETUP_DIRS:
            if not os.path.isdir(directory):
                continue
            python = os.path.join(directory, ".venv", "bin", "python3")
            if not os.path.exists(python):
                failures.append(f"not found: {python}")
                continue
            r = subprocess.run([python, "--version"], capture_output=True, text=True)
            if r.returncode != 0:
                failures.append(f"{python}: exit {r.returncode}: {r.stderr.strip()}")
        assert not failures, "\n".join(failures)

    def t_pylsp_installed():
        pylsp_bin = f"{CONTAINER_HOME}/.local/bin/pylsp"
        r = subprocess.run(
            [
                "lxc", "exec", container,
                f"--user={CONTAINER_UID}",
                f"--env=HOME={CONTAINER_HOME}",
                "--",
                pylsp_bin, "--version",
            ],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"pylsp not found in container at {pylsp_bin}: {r.stderr.strip()}"

    def t_pylsp_lsp_config():
        container_config = f"{CONTAINER_HOME}/.copilot/lsp-config.json"
        r = subprocess.run(
            ["lxc", "exec", container, "--", "cat", container_config],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"lsp-config.json not found in container at {container_config}"
        config = json.loads(r.stdout)
        servers = config.get("lspServers", {})
        assert "python" in servers, (
            f"'python' server missing from lspServers: {servers}"
        )
        assert servers["python"]["command"] == "pylsp", (
            f"unexpected command: {servers['python']['command']!r}"
        )

    tests = [
        ("Container running", t_running),
        ("build-essential installed", t_build_essential),
        ("gh installed", t_gh_installed),
        ("passwordless sudo works", t_passwordless_sudo),
        ("uv installed", t_uv_installed),
        ("dev mount readable", t_dev_mount_read),
        ("dev mount ownership transparent", t_dev_ownership),
        (".github mount works", t_github_mount),
        ("Write transparency", t_write_transparency),
        (f"container user is {CONTAINER_USER!r}", t_container_user),
        ("pylsp installed", t_pylsp_installed),
        ("pylsp registered in lsp-config.json", t_pylsp_lsp_config),
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
        print("  Packages: build-essential, gh, astral-uv")
        print("  sudo: passwordless for container user")
        print("  Next: run 'gh auth login', 'gh copilot', and '/allow-all'")
        print(
            " PAT token perms: "
            "all repos, actions, issues, merge queues, metadata, pull requests"
        )
        print("            user: copilot, gists")
        print(f"  pylsp: installed in container (~/.local/bin), config at {LSP_CONFIG_PATH}")
        print(f"  Craft setup: run './setup-container.py {container.split('-')[-1]} --setup-crafts' when ready")
        print(f"All {total} tests passed.")
        print("=" * 60)
    else:
        print(f"{passed}/{total} tests passed. See failures above.")
        sys.exit(1)


def run_craft_setup_tests(container):
    print("\n── Craft setup verification ────────────────────────────────────")

    def t_venv_exists():
        missing = []
        for directory in MAKE_SETUP_DIRS:
            if not os.path.isdir(directory):
                continue
            venv = os.path.join(directory, ".venv")
            r = subprocess.run(
                ["lxc", "exec", container, "--", "ls", venv],
                capture_output=True,
            )
            if r.returncode != 0:
                missing.append(directory)
        assert not missing, f"missing .venv in: {missing}"

    def t_venv_interpreter_valid():
        """Venv Python interpreter must be executable on the host in all setup dirs."""
        failures = []
        for directory in MAKE_SETUP_DIRS:
            if not os.path.isdir(directory):
                continue
            python = os.path.join(directory, ".venv", "bin", "python3")
            if not os.path.exists(python):
                failures.append(f"not found: {python}")
                continue
            r = subprocess.run([python, "--version"], capture_output=True, text=True)
            if r.returncode != 0:
                failures.append(f"{python}: exit {r.returncode}: {r.stderr.strip()}")
        assert not failures, "\n".join(failures)

    tests = [
        ("make setup completed (.venv)", t_venv_exists),
        ("venv Python interpreters valid on host", t_venv_interpreter_valid),
    ]

    results = [check(name, fn) for name, fn in tests]
    passed = sum(results)
    total = len(results)

    print()
    if all(results):
        print(f"All {total} craft setup tests passed.")
    else:
        print(f"{passed}/{total} tests passed. See failures above.")
        sys.exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Set up an LXD container for copilot development.",
        epilog=(
            "Examples:\n"
            "  %(prog)s 1                   # create craft-llm-1\n"
            "  %(prog)s 2 --recreate        # delete and recreate craft-llm-2\n"
            "  %(prog)s 1 --setup-crafts    # run make setup in all craft dirs"
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
    parser.add_argument(
        "--setup-crafts",
        action="store_true",
        help=(
            "Run 'make setup' in all craft project directories inside an "
            "existing container, then verify the venvs."
        ),
    )
    args = parser.parse_args()

    container = f"{CONTAINER_PREFIX}-{args.number}"

    if args.setup_crafts:
        if not container_exists(container):
            print(
                f"ERROR: container '{container}' does not exist. "
                "Create it first without --setup-crafts.",
                file=sys.stderr,
            )
            sys.exit(1)
        run_make_setup(container)
        run_craft_setup_tests(container)
        return

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
    install_pylsp(container)
    run_tests(container)


if __name__ == "__main__":
    main()
