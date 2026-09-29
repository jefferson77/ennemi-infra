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
roles/certbot/           # Let's Encrypt certificates, obtained and renewed
roles/dragonfly/         # Dragonfly (redis replacement), as a systemd service
roles/postgres/          # PostgreSQL 18, from the Ubuntu archive
roles/nodejs/            # Node.js from NodeSource, one major line (ennemi-vps)
roles/ennemi_web_api/    # ennemi-web's live-state API, as a systemd service
roles/ennemi_webapp/     # the show webapp on ennemi-brain, as a systemd service
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
./deploy certbot               # only the certbot role
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
| `GITHUB_TOKEN` | the `gh` login the `common` role sets up on every host (optional) |

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
./deploy --tags packages    # only the upgrade and the baseline toolset
./deploy --tags gh          # only the baseline toolset and the GitHub login
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
installs the baseline toolset — `git`, `htop`, `vim`, `curl`, `gh`, from
`common_packages`. Extend that list rather than installing by hand, so a
rebuilt host comes back with the same tools. Then the GitHub login (below). The
role ends with the list of hosts that need a reboot and the packages that asked
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

### The GitHub CLI (tag `gh`)

Every host gets `gh`, so each can clone, fetch and push the private
repositories. It is the Ubuntu archive's package, listed in `common_packages`
like the rest of the baseline — all three hosts run 26.04, which ships it, and
one package is not worth a third-party repository and its signing key.

The login is the `GITHUB_TOKEN` in `.env` (see [Credentials](#credentials)) — a
personal access token with at least the `repo` scope, `read:org` too for
organisation repositories. The role reads the token `gh` already holds and only
logs in when it differs, so a run is `ok` on an unchanged token and `changed`
when you rotate it, and the token itself never reaches the log (`no_log`). It
then runs `gh auth setup-git`, which points plain `git` over HTTPS at the same
credentials — so `git clone https://github.com/...` works without a prompt, not
just `gh repo clone`. Set `common_gh_setup_git=false` to leave `~/.gitconfig`
alone.

`GITHUB_TOKEN` is optional, unlike the PostgreSQL password: leave it out and
`gh` is still installed on every host, but logging in stays a manual
`gh auth login` on each one. The role says which of the two it did:

```
TASK [common : Report the GitHub CLI state] ************************
ok: [ennemi-vps] => {
    "msg": "ennemi-vps: gh version 2.46.0 (2025-12-13 Ubuntu 2.46.0-4),
    ennemi authenticated with GITHUB_TOKEN from .env"
}
```

The token is authorised for the `ennemi` account on each host, so anyone who
can reach that account can use it. It is the same trade-off as the SSH key the
`user` half authorises; scope the token to what those hosts actually need to
read.

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

| Host           | `nginx_sites`                        | `nginx_tls_sites`   | `nginx_stream_sites` | `nginx_conf_d`     |
|----------------|--------------------------------------|---------------------|----------------------|--------------------|
| `ennemi-brain` | `ennemi.net`                         | —                   | —                    | `stub_status.conf` |
| `ennemi-vps`   | `vps-placeholder`, `ennemi-web`  | `ennemi-web-tls`| —                    | —                  |
| `ennemi-dev`   | `ennemi.net-edge`                    | —                   | `ennemi.net-tls`     | —                  |

`ennemi-brain` also sets `nginx_brotli: true`: `ennemi.net` serves the webapp's pre-compressed
build with `brotli_static`.

`ennemi-brain`'s site was copied off the running host byte for byte. `ennemi.net` has
since been repointed from `/home/ennemi/webapp` to `/var/www/webapp` (its `/_nuxt/`,
`/images/` and `/audio/` aliases), where the webapp's `make deploy` now puts the build — see
*The `ennemi_webapp` role*. It also answers on `internal.ennemi.net`, a LAN-only name for
development: the venue router resolves it to `ennemi-brain`, the edge on `ennemi-dev` does not
forward it, and the letsencrypt lineage `www.ennemi.net` was expanded by hand (`dns-ovh`) to
cover it.

### Sites that need a certificate (`nginx_tls_sites`)

A site config naming `ssl_certificate` is only valid once that file exists: on a
host that has never had one, `nginx -t` fails and takes the whole play with it —
before the role that would have fetched the certificate ever runs. That is a
circle, and `nginx_tls_sites` is how it gets broken.

A site listed there carries the path it depends on, and the role deploys and
enables it **only once that path exists**:

```yaml
nginx_tls_sites:
  - name: ennemi-web-tls
    certificate: /etc/letsencrypt/live/www.ennemi.net/fullchain.pem
```

On a host without the certificate the site is left disabled and the run says so,
rather than failing:

```
TASK [nginx : Report the TLS sites still waiting for a certificate] ******
ok: [ennemi-vps] => {
    "msg": "ennemi-vps: ennemi-web-tls is not served —
    /etc/letsencrypt/live/www.ennemi.net/fullchain.pem does not exist yet.
    The certbot role fetches it, and enables the site in the same run."
}
```

The [`certbot` role](#the-certbot-role) runs straight after, and when it has
obtained a certificate it re-runs this one step (`roles/nginx/tasks/tls_sites.yml`,
a separate file for exactly that reason), so a new edge comes up in a single
deploy instead of needing a second one to notice. The gate works in both
directions: delete or revoke a lineage and the next run removes the symlink
again, which leaves nginx startable instead of wedged on a missing key.

### Other per-host switches

`nginx_webroots` is a list of document roots to make sure exist. Existence only —
no owner, group or mode, because the directory is usually written by whatever
deploys the site (`/var/www/ennemi-web` belongs to the `ennemi` account) and
taking it over as `www-data` would break that on the next run.

`nginx_brotli` installs `libnginx-mod-http-brotli-filter` and `-static`, for a
host whose sites use `brotli` or `brotli_static`. Off by default and opt-in per
host for the same reason the stream module is: those directives are a *fatal*
`nginx -t` error on a host without the modules, not a warning, so a host has
both the directives and the package or neither.

### The ennemi.net edge on `ennemi-vps`

The VPS is the public edge, and unlike `ennemi-dev` it is also the web server:
it terminates TLS itself, with its own Let's Encrypt certificate, and serves the
site from `/var/www/ennemi-web`. Nothing is forwarded to `ennemi-brain`.

The vhost is **two files**, and the split is the point:

| Port | File | Names a certificate? | Listed in |
|------|------|----------------------|-----------|
| 80  | `sites/ennemi-web`     | no  | `nginx_sites` |
| 443 | `sites/ennemi-web-tls` | yes | `nginx_tls_sites` |

A third file, `sites/ennemi-web-tailnet`, serves the same site and API under the
host's MagicDNS name; see *The tailnet vhost* below.

The plain-HTTP half names no key, so it comes up on a host that has never had a
certificate — which is what lets certbot obtain the first one. It does exactly
two things: serve `/.well-known/acme-challenge/` from `/srv/acme`,
and `301` everything else to https. The challenge location is **not** redirected
and must not be: Let's Encrypt fetches that token over plain HTTP on every
renewal, not just the first issuance.

The redirect target is `https://$host$request_uri`, so the apex stays on the
apex rather than being bounced to `www` (`ennemi-brain`'s config canonicalises
to `www` instead; either is a one-line change).

`vps-placeholder` stays enabled beside them. It owns `listen 80 default_server`
and answers anything naming a host this machine does not serve, so the real
vhost does not have to — **two default servers on one port is a fatal `nginx -t`
error**. It also keeps `/healthz` answering. Dropping it from `nginx_sites`
would not remove it either: the role never deletes a site it no longer lists, so
the stale symlink would stay and break the config in exactly that way.

Port 443 has its own catch-all, `ssl_reject_handshake on`, which needs no
certificate and aborts the handshake for any other SNI — the TLS-layer
equivalent of the `444` on port 80.

**Who owns what.** This repository owns the architecture — the directory, the
nginx sites, the certificate and its renewal. The `ennemi-web` project owns the
*contents* of the document root and nothing else. The two deploy independently
and cannot collide:

| | `ennemi-infra` (here) | `ennemi-web` |
| --- | --- | --- |
| `/var/www/ennemi-web` | creates it, never writes in it | owns everything inside it |
| `/opt/ennemi-web-api` | creates it, never writes in it | owns everything inside it, `.env` included |
| `/srv/acme` | owns — the ACME challenge webroot | cannot reach it |
| nginx sites, TLS, certbot | owns | — |
| Node, the `ennemi-web-api` unit, its state | owns | restarts the unit after a deploy |

Two things enforce that rather than just describing it. The ACME challenge
webroot is `/srv/acme`, outside `/var/www` altogether, so the content deploy's
`rsync --delete` can never remove a challenge token mid-renewal whatever its
`VPS_PATH` is. And `ennemi-web`'s `scripts/deploy.sh` refuses to *create* the
web root — a host where `nginx_webroots` has not been applied gets a clear error
instead of a site nothing is configured to serve.

**What it serves** is the `ennemi-web` build already on the host — the landing
page at `/`, the standalone `/morceau` trailer and the `/admin` page — plus one
small API under `/api/`, proxied to `ennemi-web-api` on `127.0.0.1:8787` (see
*The `ennemi_web_api` role*). It stores whether a show is running, which decides
what `/` shows. The show app itself stays on `ennemi-brain` inside the venue
network and is not reachable through this site. The content is pushed by that
project (`make deploy` rsyncs its `dist/` in) and is not managed here, ownership
included — `nginx_webroots` only makes sure the directory exists.

Writes to the API are rate-limited on the public site — ten a minute per client
address, with a burst of five, answered `429` past it — because the `/admin`
password is typed by a human and may be short, and `POST /api/admin/login` is
the one endpoint that accepts it (the panel behind it works from an HttpOnly
session cookie, not the password). Reads are never limited: every
phone on the landing page polls `GET /api/live`. The `map` and `limit_req_zone`
behind it are http-context directives, so they live in
`conf.d/ennemi-web-api.conf` (`nginx_conf_d`), not in the site.

Caching is per content type, so a deploy is visible immediately without giving
up long-lived caching where it is free:

| Path | `Cache-Control` | why |
|------|-----------------|-----|
| `/assets/` | `max-age=31536000, immutable` | hashed names — a changed file gets a changed name |
| `/images/`, `/morceau/video/`, `/morceau/audio/` | `max-age=86400` | stable names, rarely replaced |
| everything else (HTML) | `no-cache` | revalidate, so a deploy shows up at once |

`/morceau` and `/admin` without the trailing slash are a `308` to the same path
with a slash, which is the only form that resolves.

**Compression is served from disk.** The build ships `.br` and `.gz` beside
every text file, so the vhost sets `gzip_static`/`brotli_static` and nginx picks
the right variant out of `Accept-Encoding` instead of compressing the same bytes
on every request. That is what `nginx_brotli: true` on this host is for. The
images and video are already-compressed formats and are deliberately left out of
`gzip_types`.

Two details in that file worth keeping:

- **No OCSP stapling**, unlike `ennemi-brain`'s otherwise identical TLS block.
  Let's Encrypt has retired OCSP — the certificates it issues now carry no
  responder URL at all, so `ssl_stapling on` staples nothing and logs
  `"ssl_stapling" ignored, no OCSP responder URL in the certificate` on every
  reload. `openssl x509 -noout -ocsp_uri -in fullchain.pem` prints nothing.
  The `resolver` line goes with it, so the VPS needs no DNS to serve the site.
- **The security headers are repeated inside `location /assets/`** (and every
  other location that sets a header, `/api/` included). `add_header`
  is not additive across blocks: a location that sets one of its own drops every
  header from the server block. Setting only `Cache-Control` there would quietly
  strip HSTS from every asset response.

**The tailnet vhost.** On `ennemi-brain`, `www.ennemi.net` resolves to brain
itself (the venue router's internal records), so the show app cannot reach the
VPS by its public name. `sites/ennemi-web-tailnet` answers the MagicDNS names
`ennemi-vps` and `ennemi-vps.tail21508.ts.net` on port 80 instead, with the same
document root, the same `/api/` proxy (without the rate limit) and the same
`/admin` page:

```bash
curl http://ennemi-vps/api/live                     # from any tailnet host
```

It is plain HTTP on purpose — there is no public certificate for a MagicDNS
name, and WireGuard already encrypts tailnet traffic — so it sends no HSTS.
**The `allow`/`deny` is what keeps it private**: port 80 is open to the internet
for the ACME challenge, and anyone can send `Host: ennemi-vps`, so only the
tailscale ranges (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`) get past it; anything
else gets `403`. The explicit `server_name` is what routes those requests here
rather than to `vps-placeholder`'s `default_server`.

### The ennemi.net edge on `ennemi-dev`

> This is the arrangement **before** the cutover described above. Once public
> DNS points at `ennemi-vps`, this path stops receiving traffic. It is kept
> here, and on disk, because it still works and is what the domain falls back
> to if the records are pointed home again.

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
- **certificates and keys**, on `ennemi-brain` and `ennemi-dev`. The site
  configs there reference `/etc/letsencrypt/live/www.ennemi.net/`, which stays on
  the host and is renewed as it always was.
  Nothing secret is in this repo, and a host that lacks those files cannot serve
  those sites — which is what `nginx_tls_sites` turns from a failed play into a
  skipped site. On `ennemi-vps` the certificate *is* managed, by the
  [`certbot` role](#the-certbot-role); the private key still never leaves the
  host.
- the `*.bak` / `*.backup.*` files in `sites-available/` on `ennemi-brain` —
  they are not live config and were left where they are.

`nginx -t` runs after the files are written and before the reload handler
fires, so a broken config fails the play instead of reaching a live server.
Changes reload nginx rather than restarting it.

Variables: see `roles/nginx/defaults/main.yml`.

## The `certbot` role

Obtains and renews the Let's Encrypt certificates a host serves, over HTTP-01.
Only `ennemi-vps` asks for one today:

```yaml
certbot_email: nico@data4green.com

certbot_certificates:
  - name: www.ennemi.net          # the lineage — /etc/letsencrypt/live/<name>/
    domains:
      - www.ennemi.net
      - ennemi.net
```

`certbot_certificates` is empty by default and the whole role is one guarded
block, so a host that asks for nothing is left completely alone — the package is
not even installed. `certbot_email` is asserted on, not defaulted: Let's Encrypt
sends expiry warnings there, and that mail is the only notice you get that
unattended renewal has stopped working.

**It runs after nginx, and has to.** HTTP-01 is validated by fetching a file
over port 80, so nginx must already be serving `/.well-known/acme-challenge/`
before certbot can ask for anything. The `--webroot` method rather than
`--standalone`: standalone binds port 80 itself, which would mean stopping and
starting nginx on every renewal. Here nginx keeps serving and certbot only drops
a file into a directory it already publishes. `certbot_webroot` and the `root` in
`sites/ennemi-web` have to agree; both are `/srv/acme`.

`/srv/acme` is deliberately outside `/var/www`, not merely beside the site.
`ennemi-web` deploys with `rsync -a --delete` under `sudo`, so a token anywhere
that deploy can reach would be deleted if it landed between certbot writing the
token and Let's Encrypt fetching it — a renewal failure that only appears when
someone happens to deploy in that window, which is the worst kind to debug.
Keeping the two in separate trees makes it structurally impossible rather than
merely unlikely, whatever `VPS_PATH` the content deploy is given.

The issuance is guarded by `creates:`, so it runs once per lineage on a host
that does not have it and never again. Renewal is not this role's job — the
package's `certbot.timer` does it twice a day with up to twelve hours of jitter,
and re-running `certonly` on every deploy would issue duplicate certificates and
walk straight into the weekly rate limit. `--cert-name` pins the lineage, so
`/etc/letsencrypt/live/www.ennemi.net/` stays where the nginx config expects it
even if the domain list changes later.

`/etc/letsencrypt/renewal-hooks/deploy/reload-nginx` is what makes an unattended
renewal actually take effect. certbot runs it only after it has *installed* a
renewed certificate, and without it the new files sit on disk unread by the
running nginx until something else happens to reload it — which, on a host whose
config only changes when someone deploys, can easily be after it has expired.

When the issuance succeeds, the role re-runs the one nginx step that was waiting
on the certificate, so a new edge comes up in a single deploy:

```
TASK [certbot : Obtain the certificates] ****************************
changed: [ennemi-vps] => (item=www.ennemi.net)

TASK [nginx : Enable the TLS site configurations] *******************
changed: [ennemi-vps] => (item=ennemi-web-tls)

RUNNING HANDLER [nginx : Reload nginx] ******************************
changed: [ennemi-vps]
```

### Before the first issuance

Three things are outside this repository and will fail the issuance if they are
not true — HTTP-01 has no way around any of them:

1. `ennemi.net` and `www.ennemi.net` must **already resolve to the VPS's public
   address**. The certificate cannot be pre-fetched before the DNS cutover.
2. Ports **80 and 443 must be open** inbound, at the VPS provider's firewall as
   well as any local one. Nothing in this repo manages a firewall.
3. The content must be in `/var/www/ennemi-web`, or the domain serves `404`s in
   the window between DNS moving and the site arriving.

Get it wrong and you spend attempts against a rate limit of five failures per
hour, so try it against the staging CA first. Those certificates are signed by
an untrusted root — browsers reject them — but the limits are far looser:

```bash
./deploy ennemi-vps --tags=nginx,certbot -e certbot_staging=true
```

Switching back to production does **not** replace a staging certificate: the
issuance is skipped while a lineage exists, so delete it first with
`sudo certbot delete --cert-name www.ennemi.net`.

Note the `=` in `--tags=nginx,certbot`. A flag and its value have to be joined,
or the wrapper reads the value as a target of its own.

### Checking it

```bash
sudo certbot certificates        # the lineage, its names and its expiry
sudo certbot renew --dry-run     # proves the webroot path renews unattended
systemctl list-timers certbot.timer
```

Variables: see `roles/certbot/defaults/main.yml`.

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

## The `nodejs` role

Node.js on the hosts of the `web_api` and `webapp` groups (the VPS and `ennemi-brain`), from
[NodeSource](https://github.com/nodesource/distributions)'s apt repository —
one repository per major line, so the host gets `nodejs_major` (24, the major in
both `ennemi-web`'s and the webapp's `.nvmrc`) and every security release of it through apt, which
`common`'s dist-upgrade then keeps current. Ubuntu's own `nodejs` follows the
distribution's freeze instead.

An apt pin (`/etc/apt/preferences.d/nodesource`, priority 600) makes apt prefer
NodeSource's package even where the archive carries a higher-numbered one. The
role fails, with the command to fix it, on a host whose installed `node` is from
another major: `state: present` would not replace it on its own.

```bash
./deploy ennemi-vps nodejs
node --version
```

## The `ennemi_web_api` role

`ennemi-web`'s live-state API: a few hundred lines of Node with no dependencies,
which stores whether a show is running. The show app on `ennemi-brain` switches
it (`PUT http://ennemi-vps/api/live`, over the tailnet vhost), `/admin` switches
it by hand, and the landing page reads it to pick between the connection
tutorial and a bare placeholder.

| | |
|---|---|
| Code | `/opt/ennemi-web-api`, pushed by `ennemi-web`'s `make deploy` |
| Secrets | `/opt/ennemi-web-api/.env`, root `0600`, pushed with the code |
| Unit | `/etc/systemd/system/ennemi-web-api.service` |
| Runs as | a `DynamicUser`, allocated at start — no account to manage |
| State | `/var/lib/ennemi-web-api/state.json` (`StateDirectory=`) |
| Listens | `127.0.0.1:8787` — nginx is the only client |

**The split mirrors the web root's.** This role owns the directory, the unit and
the state; `ennemi-web` owns the code *and its secrets*, and restarts the unit
after each deploy. So the role creates an empty directory — existence only, as
`nginx_webroots` does — and a unit with `ConditionPathExists` on the entry
point, which is enabled straight away and simply does not start until the first
code deploy fills it. **Nothing secret is in this repository or its `.env`**: the
admin password and the show token are changed in `ennemi-web`'s `.env`, then
`make deploy` there.

The state lives under `/var/lib`, created by systemd, never in the code
directory: that one is rsynced with `--delete`. The unit is hardened beyond what
`DynamicUser` implies (no capabilities, no devices, kernel and cgroup
protection, IPv4/IPv6/Unix sockets only); `systemd-analyze security` rates it
`2.9 OK`. `MemoryDenyWriteExecute` is left off deliberately — V8's JIT needs it.

```bash
./deploy ennemi-vps --tags=nodejs,ennemi_web_api,nginx   # first time
systemctl status ennemi-web-api
journalctl -u ennemi-web-api        # one line per state change or refused write
curl http://127.0.0.1:8787/api/live
```

## The `ennemi_webapp` role

The show app itself — audience phones, the Control desk, the Stage display — on
`ennemi-brain` (the `webapp` group), behind the `ennemi.net` nginx site.

| | |
|---|---|
| Code | `/var/www/webapp`: `.output/`, pushed by the webapp's `make deploy` |
| Show content | `content/`, `public/`, `raw_assets/` in the same directory |
| Unit | `/etc/systemd/system/ennemi-webapp.service` |
| Runs as | `ennemi` — owns the directory, and is the account the deploy rsyncs as |
| State | Dragonfly (db 0), not on disk |
| Listens | `*:3000`, proxied by the `ennemi.net` site |

The app is **built on `ennemi-dev`** and only the result reaches brain, the same model
as ennemi-web on the VPS. It replaces the arrangement where `/home/ennemi/webapp` on
brain was both the git checkout and the running instance, started through nvm by a
hand-written unit; the role writes its own unit at that same path and takes it over.

**Who owns what.** The split is the ennemi-web one, with one difference that shapes the
rest: the service **writes** inside its own directory. The Control desk's Edit Spectacle
and Asset Manager tabs save to `content/`, `public/` and `raw_assets/`, which the server
resolves against its working directory, and nginx serves `public/images` and
`public/audio` straight from there.

| | `ennemi-infra` (here) | webapp repository | the running service |
| --- | --- | --- | --- |
| `/var/www/webapp` | creates it, `ennemi:ennemi 0755` | owns everything inside | — |
| `.output/` | — | rsyncs it with `--delete` | reads only |
| `content/`, `public/`, `raw_assets/` | creates them empty | rsyncs them from git | writes to them |
| the unit, Node, nginx | owns | restarts the unit after a deploy | — |

So the directory belongs to `ennemi` rather than to root or a `DynamicUser`: both the
deploy and the service write there, and nothing needs sudo to do it. The webapp deploy
guards the content directories itself — it refuses to overwrite edits made in production
until they have been pulled back into git (`make pull-content` there).

The unit is hardened with `ProtectSystem=strict` and `ReadWritePaths=` on exactly those
three directories, so the build and everything else on the host are read-only to it.
`ProtectHome=yes` is what proves nothing still reaches into `/home/ennemi/webapp`.

`nuxt.config.ts` computes its public `networkIp` from the interfaces of the machine that
runs `nuxt build`, which is now `ennemi-dev`. The unit overrides it at runtime with
`NUXT_PUBLIC_NETWORK_IP`, set to the host's own default address, as it was when brain
built itself.

As in `ennemi_web_api`, the unit carries `ConditionPathExists` on the entry point and the
restart handler waits for a build. So on a host with an empty `/var/www/webapp` the role
rewrites the unit and **restarts nothing**: whatever was already serving keeps serving
until the first deploy replaces it.

```bash
./deploy ennemi-brain --tags=nodejs,ennemi_webapp   # the directory, Node and the unit
# then, in the webapp repository:
make deploy                                         # the build, the content, the restart
./deploy ennemi-brain nginx                         # the site's aliases, onto /var/www/webapp
systemctl status ennemi-webapp
journalctl -u ennemi-webapp -f
```

Restarting the service drops every connected phone and stage display — neither this role
nor the webapp deploy should run during a show.

Variables: see `roles/ennemi_webapp/defaults/main.yml`.

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
