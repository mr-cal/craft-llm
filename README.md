# craft-llm

A script to set up an LXD containers named `craft-llm-<n>` for copilot development
on *craft* projects.

## What it does

`setup_container.py` automates the following:

1. Launches an Ubuntu LXD container called `craft-llm-<n>`.
2. Renames the default `ubuntu` user and group to match the host username,
   and moves the home directory to the same path as on the host.
   This ensures venv scripts, whose shebangs reference the host home path,
   resolve correctly in both environments without any symlinks or re-syncing.
3. Configures a 1:1 UID/GID mapping so that bind-mounted files appear owned by
   the container user inside the container and by the host user outside it.
4. Bind mounts `~/.github` and `~/dev` into the container.
5. Installs `build-essential`, the `gh` CLI, and the GitHub Copilot CLI.
6. Runs `make setup` in the snapcraft repository.
7. Runs verification tests to ensure the container is working.

## Requirements

- Ubuntu host with LXD installed and the current user in the `lxd` group.
- The `~/dev/craft/snapcraft` repository must exist on the host.
- A PAT token for authorizing copilot

## Usage

```bash
# First-time setup
python3 setup_container.py 1

# Tear down and rebuild craft-llm-1
python3 setup_container.py 1 --recreate
```

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
