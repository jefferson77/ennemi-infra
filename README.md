# ennemi — infrastructure

Ansible repository managing the two hosts of the **ennemi** project:

| Host          | Group   | Role in the project      | Connection            |
|---------------|---------|--------------------------|-----------------------|
| `ennemi-brain`| `metal` | this physical machine    | `local` (no SSH)      |
| `ennemi-vps`  | `vps`   | external VPS             | SSH                   |

Both run Ubuntu 26.04 LTS (resolute).

## Layout

```
deploy                   # wrapper: ./deploy [host|group] [role] [ansible flags]
ansible.cfg              # defaults: inventory path, roles path, SSH tuning
inventory/hosts.yml      # the single inventory
inventory/group_vars/    # per-group variables (all / metal / vps)
playbooks/site.yml       # entry point
roles/common/            # baseline shared by every host
roles/docker/            # Docker Engine + compose plugin
roles/tailscale/         # tailscale, kept at the latest release
roles/nginx/             # nginx + the per-host site configs
```

## Before the first run

1. Set the VPS address and login in `inventory/hosts.yml` and
   `inventory/group_vars/vps.yml` (both are marked `TODO`).
2. Install the collections: `ansible-galaxy install -r requirements.yml`
3. Check connectivity: `ansible all -m ping`

## Usage

The `./deploy` wrapper runs `playbooks/site.yml` and resolves its bare argument
itself: a host or group name becomes `--limit`, a role name becomes `--tags`.
Anything starting with `-` is passed straight through to `ansible-playbook`.

```bash
./deploy                       # everything, every host
./deploy vps                   # only the vps group
./deploy ennemi-brain          # only that host
./deploy common                # only the common role
./deploy docker                # only the docker role
./deploy tailscale             # only the tailscale role
./deploy nginx                 # only the nginx role
./deploy vps common            # both restrictions at once
./deploy --check --diff        # dry run
./deploy --tags user           # only part of a role (see below)
./deploy vps -e common_apt_autoclean=false
./deploy --help                # usage + the list of known hosts/groups/roles
```

Or call `ansible-playbook` directly:

```bash
# everything
ansible-playbook playbooks/site.yml --ask-become-pass

# one group only
ansible-playbook playbooks/site.yml --limit vps
ansible-playbook playbooks/site.yml --limit metal --ask-become-pass

# dry run
ansible-playbook playbooks/site.yml --check --diff
```

`--ask-become-pass` is only needed for accounts whose sudo asks for a password.

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
ordered from, and on `ennemi-brain` it would kill the controller running the
playbook — so rebooting stays a manual step:

```bash
ansible ennemi-vps -b -m reboot     # over SSH
sudo reboot                         # on ennemi-brain, from a shell
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
host gets is declared in `inventory/group_vars/`, and the files themselves live
in the role:

```
roles/nginx/files/sites/     -> /etc/nginx/sites-available/ (then symlinked)
roles/nginx/files/conf.d/    -> /etc/nginx/conf.d/
```

| Group   | `nginx_sites`              | `nginx_conf_d`      |
|---------|----------------------------|---------------------|
| `metal` | `ennemi.net`, `dev.ennemi.net` | `stub_status.conf` |
| `vps`   | `vps-placeholder`          | —                   |

`ennemi-brain`'s two files were copied off the running host byte for byte, so
applying the role there reports `changed=0` and never reloads it. The VPS gets
a placeholder default server instead: `200` on `/healthz`, `204` everywhere
else, to be replaced when it has a real job.

The role deliberately does **not** manage:

- **`nginx.conf`** — on `ennemi-brain` it is identical to the packaged
  conffile, so the distribution stays in charge of it.
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
