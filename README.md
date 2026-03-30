# craft-llm

A script to set up an LXD container named `craft-llm` for copilot development
on *craft* projects (snapcraft, rockcraft, charmcraft, etc.).

## What it does

`setup_container.py` automates the following:

1. Launches an Ubuntu 24.04 LTS LXD container called `craft-llm`.
2. Renames the default `ubuntu` user and group to match the host username
   (for example, `callahan.kovacs@canonical.com`), and moves the home directory
   to the same path as on the host (for example,
   `/home/callahan.kovacs@canonical.com`). This ensures venv scripts — whose
   shebangs reference the host home path — resolve correctly in both
   environments without any symlinks or re-syncing.
3. Configures a 1:1 UID/GID mapping so that bind-mounted files appear owned by
   the container user inside the container and by the host user outside it.
4. Adds three bind mounts from the host into the container:

   | Host path       | Container path        |
   |-----------------|-----------------------|
   | `~/.github`     | `~/.github`           |
   | `~/.copilot`    | `~/.copilot`          |
   | `~/dev`         | `~/dev`               |

5. Installs `build-essential`, the `gh` CLI, and the GitHub Copilot CLI.
6. Runs `make setup` in the snapcraft repository.
7. Runs 12 verification tests and prints a clear PASS/FAIL for each.

## Requirements

- Ubuntu host with LXD installed and the current user in the `lxd` group.
- The `~/dev/craft/snapcraft` repository must exist on the host.

## Usage

```bash
# First-time setup
python3 setup_container.py

# Tear down and rebuild the container
python3 setup_container.py --recreate

## Development

The project uses [ruff](https://docs.astral.sh/ruff/) for formatting and
linting, and [ty](https://github.com/astral-sh/ty) for type checking.

Install both with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install ruff
uv tool install ty
```

Then:

```bash
make format   # auto-format with ruff
make lint     # lint (ruff) and type-check (ty)
```
