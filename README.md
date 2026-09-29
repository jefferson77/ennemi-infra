# ennemi — infrastructure

Ansible repository managing the three hosts of the **ennemi** project:

| Host          | Group  | Role in the project           | Connection            |
|---------------|--------|-------------------------------|-----------------------|
| `ennemi-dev`  | `dev`  | development + deploy machine  | `local` (no SSH)      |
| `ennemi-brain`| `prod` | production, physical machine  | SSH (tailscale IP)    |
| `ennemi-vps`  | `prod` | production, external VPS      | SSH (tailscale IP)    |

All run Ubuntu 26.04 LTS (resolute).

`ennemi-dev` is the controller: it runs the playbooks and manages itself over a
local connection. The two production hosts are reached by their tailscale IP, so
a production deploy needs this machine on the tailnet.

## Layout

```
deploy                   # wrapper: ./deploy [host|group] [role] [ansible flags]
.env                     # credentials — git-ignored, never committed
.env.example             # the committed template for it
ansible.cfg              # defaults: inventory path, roles path, SSH tuning
inventory/hosts.yml      # the single inventory
inventory/group_vars/    # per-group variables (all / prod)
inventory/host_vars/     # per-host variables (one file per host)
playbooks/site.yml       # entry point
roles/common/            # baseline shared by every host
roles/docker/            # Docker Engine + compose plugin
roles/tailscale/         # tailscale, kept at the latest release
roles/nginx/             # nginx + the per-host site configs
roles/dragonfly/         # Dragonfly (redis replacement), as a systemd service
roles/postgres/          # PostgreSQL 18, from the Ubuntu archive
roles/dev/               # development-machine toolchain (ennemi-dev only)
```

## Before the first run

1. Install Ansible on the controller, if it is not there yet:
   `sudo apt install ansible` (or `pipx install ansible-core`).
2. Install the collections: `ansible-galaxy install -r requirements.yml`
3. Create the credentials file: `cp .env.example .env`, then fill it in.
   Nothing runs without it — see [Credentials](#credentials).
3. Join the tailnet, so the production hosts are reachable: `tailscale status`
4. Check connectivity: `ansible all -m ping`

## Usage

The `./deploy` wrapper runs `playbooks/site.yml` and resolves its bare argument
itself: a host or group name becomes `--limit`, a role name becomes `--tags`.
Anything starting with `-` is passed straight through to `ansible-playbook`.

```bash
./deploy                       # everything, every host
./deploy prod                  # only the two production hosts
./deploy dev                   # only this machine
./deploy ennemi-brain          # only that host
./deploy common                # only the common role
./deploy docker                # only the docker role
./deploy tailscale             # only the tailscale role
./deploy nginx                 # only the nginx role
./deploy dragonfly             # only the dragonfly role
./deploy cache                 # every role, on the hosts that run the cache
./deploy postgres              # only the postgres role
./deploy database              # every role, on the hosts that run a database
./deploy --tags=dev            # only the dev role (see the note below)
./deploy prod common           # both restrictions at once
./deploy --check --diff        # dry run
./deploy --tags user           # only part of a role (see below)
./deploy prod -e common_apt_autoclean=false
./deploy --help                # usage + the list of known hosts/groups/roles
```

Or call `ansible-playbook` directly:

```bash
# everything
ansible-playbook playbooks/site.yml --ask-become-pass

# one group only
ansible-playbook playbooks/site.yml --limit prod
ansible-playbook playbooks/site.yml --limit dev --ask-become-pass

# dry run
ansible-playbook playbooks/site.yml --check --diff
```

`--ask-become-pass` is only needed for accounts whose sudo asks for a password.

`dev` names both a group and a role, and the wrapper reads a bare `dev` as the
group — `./deploy dev` stays "every role on this machine". Select the role
itself with `--tags=dev` (a flag and its value have to be joined with `=`, the
wrapper would otherwise read the value as a target of its own).

## Credentials

Nothing secret is in this repository. Credentials live in `.env` at the root of
the checkout, which is git-ignored; `.env.example` is the committed template:

```bash
cp .env.example .env      # then fill in the real values
```

| Key | Used by |
|-----|---------|
| `POSTGRES_APP_PASSWORD` | the `ennemi` PostgreSQL role that owns the `ennemi` database |

`inventory/group_vars/all.yml` is the only place that reads the file, with an
`ini` lookup in `properties` mode, and the path is derived from the inventory
rather than the working directory so a run behaves the same from anywhere. The
file is read **directly, not through the shell**, so `./deploy` and a bare
`ansible-playbook` behave identically and nothing has to be exported first.

Two things about the format: values are **not quoted** — quotes would be read
as part of the password — and `#` only starts a comment at the beginning of a
line, so it is safe inside a value.

A missing file or key is an empty value rather than an error, and the roles
assert on it, so a fresh clone fails in a second with something you can act on
instead of a traceback:

```
TASK [postgres : Fail early if the application password is not set] ****
fatal: [ennemi-dev]: FAILED! => "postgres_app_password is empty. Credentials
are read from /home/ennemi/ennemi-metal/.env, which is not in git: copy
.env.example to .env and set POSTGRES_APP_PASSWORD in it."
```

Rotating a password is editing `.env` and re-running the role: the change is
applied to the cluster and reported as `changed`.

## The `common` role

Two independent halves, each with its own tag, so you can run one without the
other:

```bash
./deploy --tags user        # only the account
./deploy --tags packages    # only the upgrade
```

### The `ennemi` account (tag `user`)

Every host gets an `ennemi` account: `/bin/bash`, member of `sudo`, the
controller's `~/.ssh/id_rsa.pub` in its `authorized_keys`, and a
`/etc/sudoers.d/ennemi` NOPASSWD rule (`visudo`-validated before it is
written). So `ssh ennemi@vps.ennemi.net` works with the same key on both hosts.

It is safe on `ennemi-brain`, where the account already exists: `append: true`
keeps its existing groups and `exclusive: false` keeps any key already
authorised. Set `common_user_passwordless_sudo=false` to remove the sudoers
rule and fall back to password-prompting sudo.

### Package maintenance (tag `packages`)

Runs `apt update`, then `apt dist-upgrade` (with autoremove/autoclean), then
installs the baseline toolset — `git`, `htop`, `vim`, `curl`, from
`common_packages`. Extend that list rather than installing by hand, so a
rebuilt host comes back with the same tools. The role then
ends with the list of hosts that need a reboot and the packages that asked
for it:

```
TASK [common : List the hosts that need a manual reboot] ***********
ok: [ennemi-brain] => (item=ennemi-vps) => {
    "msg": "ennemi-vps needs a reboot (libc6, linux-image-generic)"
}
```

**The role never reboots anything.** A reboot cuts the SSH session it was
ordered from, and on `ennemi-dev` it would kill the controller running the
playbook — so rebooting stays a manual step:

```bash
ansible ennemi-vps -b -m reboot     # over SSH
sudo reboot                         # on ennemi-dev, from a shell
```

Variables: see `roles/common/defaults/main.yml`.

## The `docker` role

Installs Docker Engine and the compose plugin from Docker's own apt repository
(Ubuntu's `docker.io` lags behind and ships no compose plugin): the signing key
lands in `/etc/apt/keyrings/docker.asc`, the repository is written as a deb822
source pinned to the host's own suite and apt architecture, then `docker-ce`,
`docker-ce-cli`, `containerd.io`, `docker-buildx-plugin` and
`docker-compose-plugin` are installed and the service enabled. The role ends by
running `docker compose version` as a smoke test.

`ennemi` is added to the `docker` group, so `docker compose up` works without
sudo:

```bash
./deploy docker
ssh ennemi@vps.ennemi.net
cd ~/mystack && docker compose up -d
```

**Membership of the `docker` group is root-equivalent** — a member can start a
container that bind-mounts `/` and edit anything on the host. That is fine for
`ennemi`, which already has passwordless sudo, but do not extend
`docker_users` to an account you would not trust with root.

A shell that was already open when the role ran does not have the new group
yet: log out and back in, or use `sg docker -c '...'`.

Variables: see `roles/docker/defaults/main.yml`.

## The `tailscale` role

Keeps `tailscale` at the newest release Tailscale publishes: their signing key
in `/usr/share/keyrings/tailscale-archive-keyring.gpg`, the repository as a
deb822 source pinned to the host's suite and apt architecture, then the package
at `state: latest` and `tailscaled` enabled. Every run upgrades to whatever is
current; set `tailscale_package_state` to a version string to freeze it.

It adopts a host that was set up by hand: the role writes
`/etc/apt/sources.list.d/tailscale.sources` and deletes the one-line
`tailscale.list` their install script leaves behind, so apt does not see the
same repository twice.

**The role never runs `tailscale up`.** Joining a tailnet needs an auth key or
an interactive login, so it only reports the backend state:

```
ennemi-brain: tailscale 1.102.3, backend Running.
ennemi-vps: tailscale 1.102.3, backend NeedsLogin. Run `sudo tailscale up` on it to join the tailnet.
```

Variables: see `roles/tailscale/defaults/main.yml`.

## The `nginx` role

Installs nginx and deploys a per-host set of site configurations. Which sites a
host gets is declared in `inventory/host_vars/`, and the files themselves live
in the role:

```
roles/nginx/files/sites/     -> /etc/nginx/sites-available/   (then symlinked)
roles/nginx/files/streams/   -> /etc/nginx/streams-available/ (then symlinked)
roles/nginx/files/conf.d/    -> /etc/nginx/conf.d/
```

| Host           | `nginx_sites`                  | `nginx_stream_sites` | `nginx_conf_d`     |
|----------------|--------------------------------|----------------------|--------------------|
| `ennemi-brain` | `ennemi.net`, `dev.ennemi.net` | —                    | `stub_status.conf` |
| `ennemi-vps`   | `vps-placeholder`              | —                    | —                  |
| `ennemi-dev`   | `ennemi.net-edge`              | `ennemi.net-tls`     | —                  |

`ennemi-brain`'s two files were copied off the running host byte for byte, so
applying the role there reports `changed=0` and never reloads it. The VPS gets
a placeholder default server instead: `200` on `/healthz`, `204` everywhere
else, to be replaced when it has a real job.

### The ennemi.net edge on `ennemi-dev`

Public DNS for `ennemi.net` and `www.ennemi.net` points at this site's address,
but the web server is on `ennemi-brain`. `ennemi-dev` forwards to it over the
tailnet, and the two ports it listens on are handled at different layers on
purpose.

**There is another proxy in front.** A Caddy at `192.168.110.5` holds the
public 80 and 443, terminates TLS itself, and forwards to `ennemi-dev:80` over
plain HTTP with `X-Forwarded-Proto: https`. So in practice public traffic
arrives here on **port 80 already decrypted**, and this machine is the middle
of a three-hop chain, not the outermost edge:

```
client -> Caddy 192.168.110.5 (terminates TLS) -> ennemi-dev:80 -> ennemi-brain:80
```

Port 443 here is for clients that reach this box directly instead — a LAN
client whose DNS points at it, or the public path if Caddy is ever taken out.

| Port | Layer | File | What reaches `ennemi-brain` |
|------|-------|------|-----------------------------|
| 80  | HTTP (`http` context)   | `sites/ennemi.net-edge` | A proxied request with `Host`, `X-Real-IP` and `X-Forwarded-For` |
| 443 | TCP (`stream` context)  | `streams/ennemi.net-tls` | The bytes of the connection, untouched |

**443 is passed through, not terminated.** `ennemi-dev` holds no certificate
for the domain and needs none: it reads the SNI name out of the opening
handshake (`ssl_preread`, which requires no key) and copies the connection to
`ennemi-brain`, which completes the handshake with its own letsencrypt
certificate and renews it exactly as before. Only `ennemi.net` and
`www.ennemi.net` are forwarded; any other SNI, and a connection with no SNI,
maps to an empty upstream and is dropped. The price is the client's address:
`ennemi-brain` sees `ennemi-dev`'s tailscale IP as the peer, and at that layer
there is no header to record the real one in.

**80 forwards `X-Forwarded-Proto` exactly as it arrived.** `ennemi-brain`'s
`ennemi.net` config redirects to https only for a request carrying no
`X-Forwarded-Proto` — that is how it tells a genuine plain-HTTP client from one
whose TLS a proxy already terminated, and getting it wrong is what caused the
redirect loop documented at the top of that file. Both kinds of client reach
port 80 here, so both have to survive the hop:

| Arrives on port 80 with | Sent to brain | Brain answers |
|---|---|---|
| `X-Forwarded-Proto: https` (from Caddy) | unchanged | the app, `200` |
| no such header (direct plain-HTTP client) | nothing — nginx omits an empty header | `301` to https |

`proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;` gives both rows,
because nginx drops a header whose value evaluates empty.

Setting it to `""` unconditionally instead — on the reasoning that nothing is
terminated *here*, so port 80 must mean plain HTTP — is what produced a live
`ERR_TOO_MANY_REDIRECTS`: it stripped the header off the Caddy-terminated https
clients, brain read them as plain HTTP and redirected them to the https they
were already using. "Arrived on port 80" does not mean "arrived over plain
HTTP" when something in front terminated TLS.

A `server` that does not name one of the two hostnames answers `444` and closes.

The role deliberately does **not** manage:

- **`nginx.conf`**, with one exception — on `ennemi-brain` and `ennemi-vps` it
  is identical to the packaged conffile, so the distribution stays in charge of
  it. A host with a non-empty `nginx_stream_sites` gets one managed block
  appended, opening a `stream` context that includes `streams-enabled/`, plus
  the `libnginx-mod-stream` package that provides the context. `stream` is a
  top-level sibling of `http`, so unlike `conf.d` and `sites-enabled` there is
  no packaged include point to drop it into.
- **certificates and keys.** The site configs reference
  `/etc/letsencrypt/live/www.ennemi.net/` and `/etc/nginx/ssl/`, which stay on
  the host. Nothing secret is in this repo, and a host that lacks those files
  cannot serve those sites.
- the `*.bak` / `*.backup.*` files in `sites-available/` on `ennemi-brain` —
  they are not live config and were left where they are.

`nginx -t` runs after the files are written and before the reload handler
fires, so a broken config fails the play instead of reaching a live server.
Changes reload nginx rather than restarting it.

Variables: see `roles/nginx/defaults/main.yml`.

## The `dragonfly` role

[Dragonfly](https://dragonflydb.io) is a drop-in Redis replacement: the same
wire protocol and commands, one multi-threaded process instead of one core.

It was installed on `ennemi-brain` by hand — a release binary in
`/opt/dragonfly`, a unit written into `/etc/systemd/system` — and the role
**describes that installation rather than replacing it**, so it can reproduce
the same service on `ennemi-dev`. Both hosts are in the `cache` group and get
identical settings:

| | |
|---|---|
| Binary | `/opt/dragonfly/dragonfly`, `root:root 0755`, from the upstream release tarball |
| Unit | `/etc/systemd/system/dragonfly.service` |
| Runs as | `ennemi:ennemi` — the admin account, not a system user |
| Data | `/var/lib/dragonfly`, holding the `dump-*.dfs` snapshots |
| Listens | `0.0.0.0:6379` |
| Snapshots | every six hours (`--snapshot_cron "0 */6 * * *"`) |

```bash
./deploy dragonfly                   # both hosts
./deploy ennemi-dev dragonfly        # just this machine
systemctl status dragonfly
redis-cli -p 6379 ping
```

Every flag is a variable and the defaults are `ennemi-brain`'s live values, so
`roles/dragonfly/defaults/main.yml` doubles as the record of what production
runs. The unit template renders **byte for byte** what that host already has —
it carries no "managed by ansible" header for exactly that reason, since a
header would mean restarting production to write a comment.

### Versions

There is no apt repository for Dragonfly, so the role installs the binary from
the release tarball. `dragonfly_version` is empty by default, which means **the
newest upstream release**: it is resolved once per run from the
`releases/latest` redirect (not the GitHub API, which rate-limits anonymous
callers), then applied to every host of the group so one deploy cannot leave
the two on different versions.

**Upstream therefore decides when the service restarts.** A release lands, the
next deploy installs it and the datastore restarts, reloading from its
snapshot. Set `dragonfly_version: "1.40.1"` — in the defaults, or in
`inventory/host_vars/` for one host — to freeze it.

A host already running the target version is left completely alone: no
download, no re-extraction, no ownership or mode "fixes" over an installation
that is already correct. When an upgrade does happen, the new binary is
unpacked to `/var/tmp` and **run once before it is installed**, the way
`nginx -t` runs before nginx is reloaded, so a binary that cannot execute on
the host fails the play while the old one is still serving. It is then swapped
in by rename — legal while the old one is executing — and the service is
restarted.

The run ends with a real protocol round-trip rather than `systemctl is-active`,
because after an upgrade only a query proves the new binary came back:

```
TASK [dragonfly : Report the running Dragonfly] ********************
ok: [ennemi-brain] => {
    "msg": "ennemi-brain: dragonfly_version:df-v1.40.1 answering on 127.0.0.1:6379."
}
```

### Two things to know

- **The datastore is reachable on every interface with no password.** That is
  how `ennemi-brain` was set up and what `ennemi-dev` now mirrors — a deliberate
  choice, recorded here rather than left as a surprise. Restricting a host to
  the loopback is one line in `inventory/host_vars/`:
  `dragonfly_bind: 127.0.0.1`.
- **Upstream publishes no checksum** beside the tarball, so the integrity of a
  download rests on TLS to github.com alone.

The role does **not** manage authentication, TLS, or replication; none of them
are configured on `ennemi-brain` today.

Variables: see `roles/dragonfly/defaults/main.yml`.

## The `postgres` role

PostgreSQL 18 on `ennemi-brain` (production) and `ennemi-dev` (development),
through the `database` group — the VPS has no database of its own. Both hosts
get the same cluster; what differs between them belongs in
`inventory/host_vars/`.

The packages come from **Ubuntu's own archive**, not from `apt.postgresql.org`:
26.04 ships `postgresql-18` in `main` (18.6, with security updates), so the
`common` role's `dist-upgrade` already keeps it current. That is the difference
with the `docker` and `tailscale` roles, which need a vendor repository because
the distribution ships nothing usable.

```bash
./deploy postgres                    # the role, on both database hosts
./deploy ennemi-dev postgres         # just this machine
sudo -u postgres psql                # peer auth over the unix socket
```

Installing the package creates the `main` cluster, in
`/var/lib/postgresql/18/main` with its configuration in
`/etc/postgresql/18/main`. The role recreates it with `pg_createcluster` if it
is ever missing, but normally never has to.

**`postgresql.conf` is the distribution's.** `pg_createcluster` generates it
and ends it with `include_dir = 'conf.d'`, so the role writes only the settings
this project owns, as `conf.d/10-ennemi.conf`. Being included last, they win
over everything above them:

```yaml
postgres_settings:
  listen_addresses: localhost
  port: 5432
```

Per-host tuning (`shared_buffers`, `work_mem`, `max_connections`, …) goes into
that same dict in `inventory/host_vars/`; everything left out keeps its
packaged default.

### Reaching it from another machine

Both hosts accept connections from anywhere. That takes two settings, and
missing either one is the usual reason a remote client cannot connect:

```yaml
postgres_settings:
  listen_addresses: 0.0.0.0     # what the server binds

postgres_hba_entries:           # who is then allowed to authenticate
  - contype: host
    databases: all
    users: all
    address: 0.0.0.0/0
    method: scram-sha-256
```

`pg_hba.conf` is **not** templated — the rules in `postgres_hba_entries` are
edited into the packaged file in place, so the distribution's own rules and
comments stay exactly as they were, and that list is the only part of the file
the role owns. Rules are appended rather than re-sorted: pg_hba is evaluated
top-down and the packaged rules are already the more specific ones, so
appending is both the smallest diff and the right order. A change there
reloads the cluster; a change to `listen_addresses` restarts it.

`0.0.0.0` is IPv4 only. Use `*` and add a `::/0` rule to serve IPv6 too.

> **This is an open door with a password on it.** Any host that can route to
> `ennemi-brain` may try to authenticate, and neither host runs a firewall, so
> the strength of `POSTGRES_APP_PASSWORD` is the whole of the defence. If you
> want the reach without the exposure, one line narrows it to the tailnet:
> `address: 100.64.0.0/10`.

A configuration change restarts the cluster, since `listen_addresses` and
`port` only take effect at startup. `postgres -C` parses the new configuration
first, the way `nginx -t` does, so a typo fails the play before the server is
stopped. The run then ends by querying the cluster it leaves behind:

```
TASK [postgres : Report the running cluster] ***********************
ok: [ennemi-brain] => {
    "msg": "ennemi-brain: PostgreSQL 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1) ... on localhost:5432."
}
```

That last query is not decoration: the packaged `postgresql@18-main.service`
starts with `ExecStart=-`, so systemd reports success even when the server
failed to come up. Only a real connection proves the cluster is back.

### The application database

The role creates the database the application connects to, and the account it
connects as:

```yaml
postgres_app_database: ennemi
postgres_app_user: ennemi
postgres_app_password: ""      # from POSTGRES_APP_PASSWORD in .env
```

The account **owns** the database, which is the whole of "full access": an
owner needs no `GRANT`, and since PostgreSQL 15 the `public` schema follows the
database owner rather than being writable by everyone, so it can create tables
without a further grant. Encoding and locale are inherited from `template1`
(UTF8, `en_US.UTF-8`) rather than restated, which removes a way for the two to
disagree.

Creating them is not the same as proving they work, so the role then logs in
**the way the application will** — over TCP, with the password, through
`pg_hba.conf`'s `scram-sha-256` rule:

```
TASK [postgres : Report the application database] ******************
ok: [ennemi-dev] => {
    "msg": "ennemi-dev: application login ennemi@ennemi works over 127.0.0.1:5432."
}
```

The password is **not** in this repository: it comes from
`POSTGRES_APP_PASSWORD` in `.env`, which is git-ignored — see
[Credentials](#credentials). Changing it there and re-running the role rotates
it on the cluster.

These tasks need `python3-psycopg2` on the host, which the role installs. A
`--check` run against a host that does not have it yet cannot reach the cluster
— the install was only simulated — so the role says so and skips them instead
of failing the dry run.

The role deliberately does **not** manage:

- **the rest of `pg_hba.conf`.** The packaged rules — peer on the unix socket,
  `scram-sha-256` from localhost — are left alone; only `postgres_hba_entries`
  is managed.
- **firewalling.** Neither host runs one today, and whether port 5432 is
  reachable from outside the network is a matter for the router, not ansible.
- **schema and migrations** inside the application database — those belong to
  the application, not to the host.
- **backups.** `pg_dump@.timer` ships with the packages, unused.

Variables: see `roles/postgres/defaults/main.yml`.

## The `dev` role

The one role that is not applied everywhere: `playbooks/site.yml` runs it in a
second play scoped to the `dev` group, so it only ever touches `ennemi-dev`.

It installs the tools that belong on the development and deploy machine but
have no place on a production host — currently `make`. Everything else that
machine needs comes from `common` like on any other host, so this list stays
short:

```yaml
dev_packages:
  - make
```

Add to that list rather than installing by hand, so a rebuilt controller comes
back with the same toolchain. The apt cache was already refreshed by `common`
earlier in the run, so the install reuses it instead of hitting the network
again.

Variables: see `roles/dev/defaults/main.yml`.
