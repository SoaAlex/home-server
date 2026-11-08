# Boot check

`nas-boot-check` answers one question after a reboot or an update: did
everything come back by itself? It compares the live NAS with a recorded
healthy state and posts a verdict on Discord. The tool is a single stdlib
Python file, `nas_boot_check.py`, and a wrapper in `~/.local/bin`.

## Commands

The wrapper `nas-boot-check` takes four subcommands. Only `boot` changes
anything on the box, and only by starting units and containers that the
recorded state expects to be up.

| Command | What it does |
| --- | --- |
| `run [--json]` | One pass over every check. Exits 1 on a failure. |
| `boot [--force] [--heal]` | Waits up to 15 minutes for green, starts what is missing, posts the verdict on Discord. |
| `snapshot [--critical-port N/tcp]` | Records the current healthy state as the expectation. |
| `show` | Prints the recorded expectation. |

## What it checks

Each check compares the live system with the expectation recorded by
`snapshot`, so there is no hand-maintained list.

- Mounts, RAID state, and disk usage.
- Containers with a restart policy: running, healthy, not in a crash loop.
- Listening ports and HTTP answers of the published services.
- Network interfaces, the macvlan route and DNS answer, outbound Internet.
- System units, and units that must stay off after an update.
- Root-level customizations that an update could wipe.
- The user manager, the linger flag, the user services and timers.
- The Hermes gateway and its Discord connection.
- Kernel and Docker versions, to flag that an update happened.

<!-- prettier-ignore -->
> [!NOTE]
> The expectation lives in `~/.local/state/nas-boot-check/expected.json`, not
> in git. It holds container names, ports, and LAN addresses. Run `snapshot`
> again after any intentional change, such as a new container or a container
> you stopped on purpose.

## Run it at every boot

UGOS mounts `/volume1`, `/volume2`, and `/home` from its own service, about 14
seconds after `systemd-user-sessions`. A lingering user manager starts earlier
than that, so it can start without `~/.config/systemd/user` and leave Hermes and
the backup timers down. The two files in `systemd/` fix that and run the check
at boot. They need root, once.

1. Copy `systemd/10-wait-mounts.conf` to
   `/etc/systemd/system/user@1000.service.d/10-wait-mounts.conf`.
2. Copy `systemd/nas-boot-check.service` to `/etc/systemd/system/`.
3. Run `systemctl daemon-reload && systemctl enable nas-boot-check`.

The drop-in waits at most three minutes for the mounts and never fails the
login. The service is `Type=simple`, so it never delays the boot. If no
Discord message arrives within about 20 minutes of a reboot, the NAS or the
check itself did not come up: log in over SSH and run `nas-boot-check run`.

## Tests

The decision logic is pure and tested offline. Run the tests from this
directory:

```sh
python3 -m unittest -v
```
