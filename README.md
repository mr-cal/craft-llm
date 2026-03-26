# craft-llm

A script to set up an LXD container named `craft-llm` for copilot development
on *craft* projects (snapcraft, rockcraft, charmcraft, etc.).

## What it does

`setup_container.py` automates the following:

1. Launches an Ubuntu 24.04 LTS LXD container called `craft-llm`.
2. Configures a 1:1 UID/GID mapping so that bind-mounted files appear owned by
   `ubuntu` inside the container and by the host user outside it.
3. Adds three bind mounts from the host into the container:

   | Host path       | Container path        |
   |-----------------|-----------------------|
   | `~/.github`     | `/home/ubuntu/.github`  |
   | `~/.copilot`    | `/home/ubuntu/.copilot` |
   | `~/dev`         | `/home/ubuntu/dev`      |

4. Installs `build-essential`, the `gh` CLI, and the GitHub Copilot CLI.
5. Runs `make setup` in the snapcraft repository.
6. Runs 10 verification tests and prints a clear PASS/FAIL for each.

## Requirements

- Ubuntu host with LXD installed and the current user in the `lxd` group.
- The `~/dev/craft/snapcraft` repository must exist on the host.

## Usage

```bash
# First-time setup
python3 setup_container.py

# Tear down and rebuild the container
python3 setup_container.py --recreate
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
