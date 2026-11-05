# Load-test observability: plan

Written 2026-11-05, after the first load test (2026-11-04, ~500 people on the open SSID
`wifi_du_spectacle`). That test was watched through the UniFi console alone: Association, DHCP
and DNS went red, and the causes had to be reconstructed afterwards from logs pulled by hand
(the `ennemi` analysis repo). The second test must be watched live, from one Grafana screen on
`ennemi-brain`, with every stage of the audience's path on it: radio → DHCP → DNS → nginx →
the show app. This document is the plan for that: which KPIs, where each one comes from, what
to build in this repository, and what to change in the webapp.

Everything below was checked on the hosts on 2026-11-05; "assumption" marks what was not.

## 1. Where things stand

### Already on ennemi-brain, installed by hand (not in this repository)

| Component | Where | Version | Notes |
|---|---|---|---|
| Prometheus | `/opt/prometheus`, unit `prometheus.service`, `127.0.0.1:9090`, served at `https://www.ennemi.net/prometheus` | 3.1.0 | 15 s scrape, 30 d retention, data in `/var/lib/prometheus/data` (472 MB) |
| node_exporter | `/opt/node_exporter`, `127.0.0.1:9100` | 1.8.2 | no textfile collector |
| nginx_exporter | `/opt/nginx_exporter`, `127.0.0.1:9113`, reads `stub_status` on `:8080` | 1.3.0 | connections and request counts only |
| Grafana | apt package, `127.0.0.1:3001`, served at `https://www.ennemi.net/grafana` | 13.2.0 | apt repo `apt.grafana.com` present but **disabled** (`grafana.list.disabled`); admin password in clear in `grafana.ini` |
| Grafana provisioning | `/etc/grafana/provisioning` | | one Prometheus datasource; folder "System Monitoring" with 3 dashboards (node, nginx, Dragonfly) |
| Dragonfly | already scraped on `:6379/metrics` | | |

All four Prometheus targets are `up`. Nothing from the UniFi side, from dnsmasq or from the
webapp reaches Prometheus today, and there is no log store at all.

### Gaps, by stage of the audience's path

- **Radio / association (APs):** nothing. The console keeps 5-minute averages for 24 h.
- **DHCP (gateway):** nothing. The UCG-Fiber's `dnsmasq-dhcp` logs every DISCOVER / OFFER /
  REQUEST / ACK / NAK to its `/var/log/daemon.log` (syslog-ng). On 2026-11-04 between 20:00
  and 22:00 CET that file holds 2613 DISCOVER, 2011 OFFER, 2103 REQUEST, 1532 ACK, **571 NAK
  and 602 "no address available"**, for a pool of ~1000 addresses (192.168.108.16–192.168.111.254,
  24 h leases; 403 leases held on 2026-11-05 afternoon). Worth a look by the DHCP investigation;
  for the test it is the one DHCP signal the dashboard must show.
- **DNS (ennemi-brain dnsmasq 2.92):** no statistics exported, no query log. dnsmasq answers
  its cache statistics over DNS (CHAOS TXT `hits.bind`, `misses.bind`, `servers.bind`, ...),
  which the standard exporter reads.
- **nginx:** `stub_status` only. The access log is the default `combined` format: no
  `$request_time`, no `$upstream_response_time`, no status by route.
- **Webapp (`ennemi-webapp`, Nuxt/Nitro on `:3000`):** counts every socket message, join,
  vote, refusal and socket error, but only prints one summary line a minute to journald
  (`specs/server-logs.md`). The spec itself names the missing piece: a `/metrics` endpoint from
  the same counters (audit finding OBS-1). No event-loop lag, no heap.
- **MediaMTX (video, snap):** `metrics: no` in its config; `api` on `:9997` answers.
- **Note:** during the first test nobody was on the show app (`peers user 1` in the journal),
  so the app has never been measured at load.

### The UniFi side

- UCG-Fiber, UniFi OS 5.1.34, Network application 10.6.109, site `default`. API reachable from
  brain (`https://192.168.108.1`). Six APs (five U7 on 8.7.11, one UK Ultra on 6.8.2) and the
  USW Flex 2.5G on the Admin VLAN (192.168.8.0/24); brain reaches them (ping OK).
- **Remote logging** (`get/setting` key `rsyslogd`): `enabled: true, this_controller: true,
  this_controller_encrypted_only: true`, no external `ip`/`port`. Devices send their syslog,
  encrypted, to the console only (`unifi` listens on UDP 5514). Nothing leaves the console.
- **SNMP:** off (`snmp.enabled: false`). `snmpd` exists on the console but is not running.
- **What the AP syslog contains** (from the logs pulled after test 1, per AP, `/var/log/messages`
  on flash for the U7s, RAM only on the UK Ultra): `hostapd` auth/assoc/deauth lines, kernel
  `wlan` lines, `wevent` join/custom events, `mcad` station anomalies, and above all `stahtd`'s
  **STA_ASSOC_TRACKER** JSON events, one per client per step: `event_type` =
  `association | success | failure | soft failure | deny | sta_leave`, with `assoc_delta`,
  `auth_delta`, `ip_delta` (time to an IP: the DHCP stage, as seen by the AP), `dns_responses`,
  `dns_timeouts`, `auth_rssi`, `sta_dc_reason`. On Lite Gauche alone, test 1 produced 572
  associations, 111 successes, 162 failures, 32 denies, 318 leaves. **This is the join funnel,
  per AP, in real time, once the APs' syslog reaches brain.** (Units of the `*_delta` fields
  look like microseconds; confirm against a known join before charting them.)
- **What the API offers** (`stat/device`, refreshed by each device's inform every few seconds):
  per radio `cu_total`, `cu_self_rx`, `cu_self_tx` (channel utilisation), `num_sta`,
  `tx_retries_pct`, `satisfaction`, `channel`, `tx_power`; per VAP `num_sta`, `avg_client_signal`,
  `ccq`, `wifi_tx_latency_mov`, tx/rx errors and drops; per AP `system-stats` (cpu, mem) and
  `uplink`; switch `port_table` (rx/tx errors, drops, PoE power per port); gateway
  `system-stats`, `network_table[].active_dhcp_lease_count`, WAN monitors. `stat/sta` per
  client: signal, satisfaction, retries, IP.

## 2. The KPIs to watch

One row per stage of the path, each with its source and what "red" looks like. The first
column is the stage a phone fails at; the point of the dashboard is to see *which* row turns
red first when the room fills.

### A. Radio and association (per AP, per radio)

| KPI | Source | Red when |
|---|---|---|
| Clients per radio (`radio_stations`), per VAP | unpoller | > 60 on one radio while another is idle (steering, placement) |
| Channel utilisation total / self-rx / self-tx | unpoller `radio_channel_utilization_*_ratio` | total > 0.6 sustained; total ≫ self → a neighbour network (AudioBackbone, lighting) |
| Transmit retry rate | unpoller `radio_transmit_retries` / `vap_transmit_retries_total` rate, `tx_retries_pct` | > 30 % |
| Average client signal per VAP, satisfaction | unpoller `vap_average_client_signal`, `vap_satisfaction_ratio` | < −75 dBm average; satisfaction < 0.6 |
| Channel and tx power per radio | unpoller `radio_channel`, `radio_transmit_power` | any change during the show (DFS radar move) |
| Join funnel events per minute per AP: association, success, failure, soft failure, deny, leave | Loki, from AP syslog `stahtd` JSON | failures + denies > 10 % of associations |
| Time to associate, time to IP, DNS timeouts per join | Loki, same lines (`assoc_delta`, `ip_delta`, `dns_timeouts`) | `ip_delta` p95 > 5 s: DHCP, not radio |
| Deauth / disassoc reasons, BSS transition | Loki, `hostapd` lines | spikes |
| AP CPU, memory, uplink errors | unpoller device stats | CPU > 80 % |
| Neighbour networks on our channels | unpoller `rogueap` metrics (`save_rogue`) | a strong one appearing on a channel we use |

### B. DHCP (gateway)

| KPI | Source | Red when |
|---|---|---|
| DISCOVER / OFFER / REQUEST / ACK / NAK per minute | Loki, gateway `dnsmasq-dhcp` lines | ACK ≪ DISCOVER; any NAK burst |
| "no address available" per minute | Loki, same | > 0 |
| Active leases vs pool size (1007 addresses) | API `network_table[].active_dhcp_lease_count` via a textfile collector (unpoller does not export it: assumption, to verify on the first scrape) | > 80 % of the pool |
| dnsmasq restarts on the gateway | Loki, `ubios-udapi-server: Got process exit event for process dnsmasq-main` | any, during the show |
| Gateway CPU, load, memory | unpoller | CPU > 70 % |

Recommendation for the test, independent of monitoring: shorten the Public network's lease
time (24 h today) so a pool of ~1000 cannot fill with randomised MACs across two evenings.

### C. DNS (ennemi-brain dnsmasq)

| KPI | Source | Red when |
|---|---|---|
| Queries/s, cache hits and misses, insertions, evictions | dnsmasq_exporter (CHAOS stats) | evictions > 0 (cache too small) |
| Queries forwarded and failed per upstream (1.1.1.1, 9.9.9.9) | dnsmasq_exporter `servers_queries`, `servers_queries_failed` | failed rate > 0 |
| Concurrent-query limit hit (`dns-forward-max`, now 2000) | Loki, journal line "Maximum number of concurrent DNS queries reached" | any |
| Per-query log (which names, which clients, SERVFAIL) | Loki, dnsmasq `log-queries` on for the test only | — |
| DNS timeouts seen by the APs | Loki, `stahtd` `dns_timeouts` | > 0 |

### D. Wired path and gateway

| KPI | Source | Red when |
|---|---|---|
| Switch port rx/tx errors, drops, speed, PoE power per port | unpoller USW metrics | errors > 0, a port falling to 100 Mbit/s |
| Gateway LAN/WAN throughput, WAN latency monitors | unpoller gateway metrics | — |

### E. Server: nginx and the host

| KPI | Source | Red when |
|---|---|---|
| nginx active / reading / writing / waiting connections, accepts vs handled | nginx_exporter | handled < accepts; active near 768 × workers |
| Requests/s by status and route group (`/`, `/_nuxt/`, `/api/`, `/api/websocket`, `/images/`, `/live/`) | Loki, nginx JSON access log | 5xx > 0, 499 (client gave up) rising |
| Request time p50 / p95 and upstream time per route group | Loki, same (`quantile_over_time`) | p95 > 1 s for `/` and `/_nuxt/` |
| WebSocket upgrades per minute (101) and their failures | Loki, same | failures |
| Host CPU, load, softirq, memory | node_exporter | load > cores |
| Conntrack entries, TCP retransmits, sockets in TIME_WAIT, open files | node_exporter (`node_nf_conntrack_entries`, `node_netstat_Tcp_RetransSegs`, `node_sockstat_*`) | conntrack > 80 % of 262144 |
| NIC packets, drops, errors on `enp86s0` | node_exporter | drops > 0 |

### F. The show app (`ennemi-webapp`)

| KPI | Source | Red when |
|---|---|---|
| Open sockets by domain (user / control / stage), audience count | new `/api/metrics` gauges | sockets ≠ clients associated − those not on the app |
| Messages in / published / sent per type, per second | new `/api/metrics` counters from `socketTraffic` | — |
| Joins, rejoins, leaves, votes, refusals by reason, sockets cut for missed pongs, socket errors | same counters (`events_total{event=...}`) | `socket-closed:no-pong` and `rejoin` rising together: phones dropping off Wi-Fi |
| Event-loop lag p99, heap used, RSS | new `/api/metrics` from `perf_hooks.monitorEventLoopDelay` and `process.memoryUsage()` | lag p99 > 100 ms |
| Dragonfly ops/s, latency, connected clients | already scraped | — |
| Show markers: Play, Stop, screen changes | Loki, journal `ennemi-webapp` lines → Grafana annotations | — |
| Video: WHEP sessions, bytes out | MediaMTX metrics (phase 2) | — |

## 3. Architecture

```
  APs (192.168.8.x)  ─┐
  USW Flex           ─┼─ syslog UDP 1514 ─► Alloy ──► Loki (127.0.0.1:3100) ◄── Grafana
  UCG-Fiber          ─┘      (rfc3164)      │  ▲                                  ▲
        ▲                                   │  └─ journald (webapp, dnsmasq,     │
        │ Network API (https://192.168.108.1)│     nginx, prometheus, ...)        │
        │                                   │  └─ /var/log/nginx/access.json.log │
     unpoller (127.0.0.1:9130) ◄────────────┼──────── Prometheus (127.0.0.1:9090) ┘
                                            │              ▲ scrapes
  node_exporter :9100 (+ textfile: DHCP leases from the API)
  nginx_exporter :9113     dnsmasq_exporter :9153     ennemi-webapp :3000/api/metrics
  dragonfly :6379/metrics  mediamtx :9998 (phase 2)
```

Everything runs on ennemi-brain, bound to localhost except the syslog listener. Choices:

- **Prometheus stays** for numbers: it is there, it works, Grafana already reads it. Retention
  stays 30 d. The UniFi job scrapes every 30 s (unpoller polls the controller on each scrape;
  the devices inform every ~10 s anyway).
- **Loki for logs**, single binary, filesystem storage, from the same `apt.grafana.com` repo
  Grafana came from. Reason: the two funnels that matter most (join events from the APs, DHCP
  from the gateway) exist only as log lines, and LogQL turns them into rates and quantiles in
  Grafana with no exporter to write. VictoriaLogs would do the same; Loki is the Grafana-native
  choice and the repo is already configured. Volume is small: test 1 produced ~300 lines/min
  per AP at the peak, so ~2000 lines/min for six APs plus the gateway.
- **Alloy** (Grafana's collector, same repo) does the three collections: the syslog listener,
  journald, and the nginx JSON log. One config, one unit.
- **Syslog on UDP 1514**, not 514: the UniFi setting takes any port, and 1514 spares the Alloy
  service a capability drop-in. Alloy stamps each line on receipt, in UTC, so the APs' and
  gateway's local-time (CET, no year) timestamps never mix with the server's UTC: the clock
  mismatch noted after test 1 goes away for everything collected live.
- **unpoller** (v5.9.4, 2026-11-04, actively released; UniFi library 6.6) polls the Network
  API and exposes the per-radio, per-VAP, switch-port and gateway metrics listed above, plus
  rogue APs. It also forwards the controller's own events (client connected, roamed,
  disconnected; alarms; anomalies) to Loki. It needs a **local read-only admin** on the console
  (not a UI/SSO account); the `UNIFI_USER` in the `ennemi` repo's `.env` logs in through the
  same `/api/auth/login`, so that account works too, but a dedicated viewer is cleaner.
- **SNMP stays off.** The API already carries what SNMP would, and the APs' syslog carries what
  the API does not. Reconsider only if the API turns out to lag at load.
- **Grafana admin password** moves from `grafana.ini` into `.env` (`GRAFANA_ADMIN_PASSWORD`),
  like every other secret here.

## 4. Changes on the UniFi console (settings, not code)

Back up `get/setting` first (the `ennemi` repo's channel tool shows the pattern). Each of these
is a change to the live network: do them before the test day, not on it.

1. **Remote logging → external syslog server:** `192.168.108.2`, port `1514`, contents:
   device logs, client logs, admin activity; keep "log to this console" on. In the API this is
   `rest/setting/rsyslogd` with `ip`, `port`, `netconsole_*`, `contents`, `debug` beside the
   existing `enabled`, `this_controller`, `this_controller_encrypted_only`, `log_all_contents`
   (field names from third-party clients of the API: assumption, confirm by reading the setting
   back after setting it in the UI once). Then check on brain that each of the six APs and the
   switch arrives (`tcpdump -ni enp86s0 udp port 1514`, then a Loki query per `device`).
2. **Does the console forward its own `daemon.log`?** The setting is documented for adopted
   devices; whether UniFi OS also forwards the UCG's own syslog-ng stream (where `dnsmasq-dhcp`
   writes) is not. Verify right after step 1. If it does not, the fallback is a small
   `unifi-gateway-log` unit on brain that runs `ssh root@192.168.108.1 tail -F /var/log/daemon.log`
   into `/var/log/unifi/gateway-daemon.log` for Alloy to tail, with an SSH key added to the
   console (Console settings → SSH keys) rather than the password. Read-only, and the gateway
   already accepts SSH from brain's network.
3. **Firewall:** devices on the Admin VLAN must reach brain on the Public VLAN, UDP 1514. The
   default zone rules allow it (brain reaches the APs today); confirm with the tcpdump above.
4. **Read-only admin for unpoller:** Settings → Admins & Users → add a local account
   (`unpoller`, role View Only, site `default`, no 2FA). Its password goes in this repository's
   `.env` as `UNPOLLER_PASSWORD`.
5. **DHCP lease time** on Public: 24 h → 2 h (see §2.B). Not monitoring, but it changes what
   the lease gauge means.

## 5. Work in this repository (ennemi-infra)

Conventions to keep: one role per service, every task `become`-explicit, `ansible-lint`
production profile (the PostToolUse hook runs it on every edited YAML), secrets read from
`.env` in `inventory/group_vars/all.yml`, binaries from upstream releases unpacked under
`/opt/<tool>/` the way `roles/dragonfly` does it (latest release resolved once on the
controller, staged in `/var/tmp`, binary checked with `--version` before it replaces the old
one), units written to be byte-identical with what the host already has so adopting an
existing install is a no-op.

### Inventory and plumbing

- `inventory/hosts.yml`: new group `monitoring` with `ennemi-brain`.
- `inventory/host_vars/ennemi-brain.yml`: the UniFi devices for log labelling (name, Admin IP,
  kind: ap / switch / gateway), so Alloy can turn a source address into `device="U7 Lite Gauche"`.
  The six APs' and the switch's addresses are in the `ennemi` repo's `HANDOFF.md`.
- `inventory/group_vars/all.yml` and `.env.example`: `GRAFANA_ADMIN_PASSWORD`, `UNPOLLER_USER`,
  `UNPOLLER_PASSWORD`.
- `playbooks/site.yml`: a play "Apply the monitoring stack" on `monitoring`, roles in order:
  `prometheus`, `exporters`, `loki`, `alloy`, `unpoller`, `grafana`, each tagged. `./deploy`
  picks the new role names up by itself.
- `README.md`: the new roles in the layout table, the new `.env` keys in the credentials table,
  a pointer to this document.
- **Ordering with the nginx role:** the nginx changes below live in the existing `nginx` role,
  which runs in the baseline play before this one; nothing in the monitoring play needs nginx
  reloaded first.

### Role `prometheus` (adopts the existing install)

- Install under `/opt/prometheus` from GitHub releases (version pinned in defaults to what is
  there, 3.1.0, so the first run changes nothing; bump deliberately).
- Unit template reproducing the current one, plus `ExecReload=/bin/kill -HUP $MAINPID` so a
  config change is a reload, not a restart (the current unit has no reload, so a scrape-config
  change today means a restart and a gap).
- `prometheus.yml` from a template: global 15 s, the existing four jobs with their labels kept,
  and the new jobs: `unpoller` (30 s, timeout 25 s), `dnsmasq_exporter`, `ennemi_webapp`
  (`127.0.0.1:3000`, `metrics_path: /api/metrics`), `alloy` (its own metrics), `loki`,
  `mediamtx` (phase 2, behind a variable). `promtool check config` as `validate:`.
- Retention as a variable (30 d).

### Role `exporters` (node, nginx, dnsmasq, textfile scripts)

- node_exporter and nginx_exporter: adopt `/opt/node_exporter` and `/opt/nginx_exporter`, same
  release pattern, same units; add `--collector.textfile.directory=/var/lib/node_exporter/textfile`
  to node_exporter.
- dnsmasq_exporter: Ubuntu package `prometheus-dnsmasq-exporter` (0.3.0, google/dnsmasq_exporter),
  pointed at `192.168.108.2:53` (dnsmasq binds the LAN address only) and at an empty leases
  file (brain's dnsmasq serves no DHCP; confirm on the first run that the exporter tolerates an
  empty file, otherwise build the upstream binary like the others). Listens on `127.0.0.1:9153`.
- `unifi-dhcp-leases`: a 30-line Python script plus a systemd timer (every 60 s) that logs in to
  the Network API with the unpoller account, reads `stat/device` for the gateway and writes
  `unifi_network_active_dhcp_leases{network="Public"}` and `unifi_network_dhcp_pool_size` to the
  textfile directory. Only if unpoller turns out not to export the lease count (check its
  gateway metrics first).

### Role `dnsmasq` (adopts `/etc/dnsmasq.d/ennemi.conf`)

Today the file is hand-edited (it was changed on 2026-11-05 for the upstreams, cache size and
`dns-forward-max`). The role templates it from variables (`listen-address`, the `ennemi.net`
override, upstreams, `cache-size`, `dns-forward-max`) and adds two test switches:
`dnsmasq_log_queries` (off by default; `log-queries=extra` for a test night) and `log-async=50`
so logging never blocks answers. `dnsmasq --test` as `validate:`. Restart, not reload: dnsmasq
has no reload; schedule the restart hours before doors open.

### Role `nginx` (existing): two additions

- `files/conf.d/log_format_json.conf`: a `log_format ennemi_json escape=json` with `time_iso8601`,
  `remote_addr`, `request_method`, `uri` (not `request_uri`: no query strings in the store),
  `status`, `body_bytes_sent`, `request_time`, `upstream_connect_time`, `upstream_response_time`,
  `http_upgrade` (to tell WebSocket upgrades apart), `host`, `http_user_agent`. Listed in
  `nginx_conf_d` for brain.
- `files/sites/ennemi.net`: a second `access_log /var/log/nginx/access.json.log ennemi_json;` in
  the server block (the combined log stays for the tools that read it; logrotate's `*.log` rule
  already covers the new file), and a `location = /api/metrics { return 404; }` above
  `location /`, so the app's metrics never leave the host (Prometheus scrapes `:3000` directly).
  `worker_connections 768` in the packaged `nginx.conf` is left alone: 22 workers × 768 is far
  above 500 sockets, and the role deliberately does not own that file.

### Role `loki`

- Package `loki` from `apt.grafana.com` (re-enable the repository: `grafana.list.disabled` →
  a `.sources` entry managed by the role, key already at `/etc/apt/keyrings/grafana.gpg`).
  Not the Ubuntu archive's `loki` 2.4.
- `/etc/loki/config.yml`: single-binary mode, `127.0.0.1:3100`, tsdb index + filesystem
  chunks under `/var/lib/loki`, compactor with 30 d retention, ingestion limits raised for the
  burst (10 MB/s, 20 MB burst), `reject_old_samples` off (the UK Ultra may replay old lines).
- Readiness check on `/ready`.

### Role `alloy`

- Package `alloy` from the same repository; `/etc/alloy/config.alloy` from a template:
  - `loki.source.syslog` on `udp 0.0.0.0:1514`, `syslog_format = "rfc3164"`,
    `use_incoming_timestamp = false`, `rfc3164_default_to_current_year = true`; relabel the
    source address to `device` and `kind` from the inventory list; `job="unifi"`.
  - `loki.process` for the UniFi stream: for `stahtd` lines extract the JSON into labels
    `event_type` and `vap` (both bounded sets; **never** `mac`), for `dnsmasq-dhcp` lines extract
    `dhcp_op` (DISCOVER…NAK, no-address), for `hostapd` lines `hostapd_event`
    (authenticated / associated / disassociated / deauthenticated).
  - `loki.source.journal` for the units `ennemi-webapp`, `dnsmasq`, `nginx`, `dragonfly`,
    `prometheus`, `loki`, `unpoller`, `alloy`, label `unit`.
  - `loki.source.file` on `/var/log/nginx/access.json.log` with a `json` stage, labels
    `status`, `route` (the route group computed by a `template` stage from `uri`), `upgrade`.
  - `prometheus.exporter.self` scraped by Prometheus (Alloy's own health).
- The `alloy` user joins `adm` and `systemd-journal` for the log files and the journal.
- A UFW rule if UFW is active on brain (`20-ufw.conf` exists in rsyslog.d; check `ufw status`
  first): allow UDP 1514 from `192.168.8.0/24` and from `192.168.108.1`.

### Role `unpoller`

- Binary from GitHub releases under `/opt/unpoller`, unit as a dedicated user.
- `/etc/unpoller/up.conf` from a template: `[unifi.defaults] url = "https://192.168.108.1"`
  (bare host, no port, as UniFi OS wants), `user`/`pass` from `.env`, `verify_ssl = false`
  (self-signed), `sites = ["default"]`, `save_sites = true`, `save_rogue = true`,
  `save_events = true`, `save_alarms = true`, `save_anomalies = true`, `save_ids = false`,
  `save_dpi = false`; `[prometheus] http_listen = "127.0.0.1:9130"`; `[loki] url =
  "http://127.0.0.1:3100"` for the events. `no_log` on the template task.
- Check: scrape `:9130/metrics` and assert `unpoller_device_info` names all seven devices.

### Role `grafana` (adopts the apt install)

- Repository as above; package `grafana` present (13.x).
- `grafana.ini` managed with `ini_file` for the few keys in use (`[server]` as today,
  `[security] admin_password` from `.env`, `no_log`), everything else left to the package.
- Provisioning from the repository: `datasources/` (Prometheus as today, plus Loki at
  `http://127.0.0.1:3100`), `dashboards/dashboards.yml` as today, and the JSON files under
  `files/dashboards/`: the three existing ones copied in as they are on disk, plus the new ones
  below. Grafana reloads provisioned dashboards every 30 s; `allowUiUpdates: true` means an
  edit made in the UI is kept in Grafana's database but not written back to disk, so the
  workflow is edit in the UI → Export → commit the JSON here.

## 6. Grafana dashboards

All provisioned from this repository, in a folder "Load test".

### "Load test — Live" (the one screen for the night; 10 s refresh, last 30 min)

1. **Funnel, one stat tile each, left to right in the order a phone goes through them:**
   clients associated (sum of `radio_stations`), DHCP ACK/min vs DISCOVER/min, NAK + no-address
   per minute, leases used / pool, DNS queries/s and forward failures, sockets open on the app
   by domain, audience count, nginx active connections, HTTP 5xx and 499 per minute.
2. **Radio, per AP:** clients per radio (bar gauge, one bar per radio, coloured by band);
   channel utilisation per radio over time with thresholds at 0.6 and 0.8; retry rate per
   radio; average client signal per VAP; a state timeline of `radio_channel` per radio, so a DFS
   move is a visible colour change.
3. **Join quality, from the AP logs:** stacked events per minute per AP by `event_type`
   (`sum by (device, event_type) (count_over_time({job="unifi", unit="stahtd"}[1m]))`);
   `ip_delta` p50/p95 per AP (`quantile_over_time` on the unwrapped field); `dns_timeouts` per
   minute; hostapd deauth/disassoc per minute per AP.
4. **DHCP and DNS:** DHCP operations per minute stacked by `dhcp_op`; NAK and no-address alone,
   in red; gateway dnsmasq exits; brain dnsmasq hits/misses/insertions/evictions rates;
   upstream queries and failures per server; the forward-max line count.
5. **Server:** nginx connections (active/reading/writing/waiting) and accepts vs handled;
   requests/min by status and by route group; request time p50/p95 per route group from the
   JSON log; WebSocket upgrades per minute; host CPU by mode, load, softirq; conntrack entries
   vs max; TCP retransmits; NIC drops; webapp event-loop lag p99, heap, RSS; messages in / out
   per type per second; events per minute by `event` with `socket-closed:no-pong`,
   `socket-error:*` and `*-rejected:*` highlighted; Dragonfly ops/s and latency.
6. **Wired and gateway:** switch port errors/drops and PoE power per port; gateway CPU, memory,
   load; LAN/WAN bps; WAN monitor latency.
7. **Annotations** from Loki: `{unit="ennemi-webapp"} |~ "Play|Stop|run started"` so the show's
   own timeline is drawn on every panel; a manual annotation for "doors open".

### "UniFi — devices" (second screen, detail per device)

The unpoller project's own dashboards (UAP insights, USW insights, client insights, network
sites) imported from grafana.com as JSON into `files/dashboards/` with the datasource variable
set to the provisioned Prometheus. They are maintained against unpoller's metric names, so
they are the fastest way to get per-device drill-down; the Live dashboard links to them.

### Logs

Grafana's Explore with Loki, three label sets worth bookmarking: `{job="unifi", device=~".+"}`,
`{unit="ennemi-webapp"}`, `{job="nginx-access"}`.

No alert rules for the test: thresholds are drawn, and someone is watching. Alerts come after,
once the normal ranges are known.

## 7. Changes in the webapp (`/home/ennemi/webapp`)

The repository's own spec already points at this ("OBS-1's `/metrics` would increment counters
from the same `count*` functions, with the same keys as labels, and read the open sockets as a
gauge", `specs/server-logs.md`, Limitations). The change stays inside that design: no new
dependency (the Prometheus text format is a dozen lines to render), no per-phone label, nothing
logged.

- `server/utils/metrics.ts`: a registry of monotonic counters and gauges, and `renderMetrics()`
  to the text exposition format (`# TYPE` lines, labels escaped). Metric names:
  `ennemi_socket_messages_in_total{type}`, `ennemi_socket_messages_published_total{type}`,
  `ennemi_socket_messages_sent_total{type}`, `ennemi_socket_events_total{event}`,
  `ennemi_socket_peers{domain}`, `ennemi_audience_count`, `ennemi_eventloop_lag_seconds{quantile="0.5|0.99"}`
  and `ennemi_eventloop_lag_max_seconds` from `perf_hooks.monitorEventLoopDelay({ resolution: 20 })`
  (reset after each scrape), `process_resident_memory_bytes`, `nodejs_heap_used_bytes`,
  `nodejs_heap_total_bytes`, `process_start_time_seconds`.
- `server/utils/socketTraffic.ts`: `countIn`, `countPublished`, `countSent`, `countEvent`,
  `peerOpened`, `peerClosed` also feed the registry, beside the per-window maps they feed today.
  The minute line is unchanged.
- `server/api/metrics.get.ts`: `GET /api/metrics`, `Content-Type: text/plain; version=0.0.4`,
  no session required (the route is unreachable from outside: nginx returns 404 for it, and
  Prometheus scrapes `127.0.0.1:3000`). The event-loop histogram is started by a Nitro plugin
  (`server/plugins/metrics.ts`) so importing the module stays inert in tests, as `redis.ts` does.
- `specs/metrics.md`: the new spec (state, behaviour, failure modes, config, code map, tests),
  and `specs/server-logs.md` loses its first limitation.
- Tests: `tests/unit/server/metrics.spec.ts` (rendering and escaping, counters that survive a
  window flush, the peers gauge following open/close, lag reset after a scrape);
  `socketTraffic.spec.ts` gains "a count reaches the registry".
- Phase 2, client side: a phone's time from page load to socket open and its reconnect count,
  reported in the existing `SESSION_DEVICE` message and counted into bounded buckets
  (`events_total{event="socket-open:<1s|<5s|>5s"}`), never per phone.
- Deployment: `make deploy` restarts the app and drops every connected phone, so it goes out
  the day before the test, never during one. The nginx `location = /api/metrics` in this
  repository must be deployed before or with it.

## 8. Order of work

Phase 1, needed for test 2 (one to two days of work, in this order so each step is checkable
on its own):

1. `prometheus`, `exporters`, `grafana` roles adopting what exists: `./deploy ennemi-brain
   --check --diff` must report no change on the first run except the new scrape jobs and the
   moved admin password.
2. `loki` + `alloy` with journald and the nginx JSON log (needs the `nginx` role change first):
   lines visible in Explore.
3. UniFi remote logging (§4.1–4.3) + Alloy's syslog listener: all seven devices visible under
   `job="unifi"`; settle whether the gateway's own log arrives, else the SSH fallback.
4. `unpoller` (§4.4 first): seven devices in `unpoller_device_info`; the DHCP lease gauge or
   its textfile fallback.
5. `dnsmasq` role + exporter.
6. Webapp `/api/metrics` (§7) and its scrape job.
7. The "Load test — Live" dashboard and the imported unpoller dashboards; walk through every
   panel with the room empty, then with a handful of phones joining and voting.
8. A dry run: one evening with the stack up, phones joining, `log-queries` on, watching for
   gaps (a target down, a label that explodes, Loki rejecting lines).

Phase 2, after test 2: MediaMTX metrics (its config is a snap, and the TODO to put it under
Ansible already exists), client-side join timings, SNMP if the API proves too slow, alert rules
with thresholds taken from the test.

## 9. Test-day runbook (draft)

- T−1 day: everything deployed, every Prometheus target `up`, one line from every UniFi device
  in Loki in the last hour, the dashboard loaded on the laptop that stays at the desk.
- T−3 h: `dnsmasq_log_queries: true` deployed (one dnsmasq restart, a few seconds of DNS).
  APs powered for more than 2 minutes (DFS check) — already in the channel plan's notes.
- T−30 min: Grafana window set to "last 30 min", refresh 10 s; note the time; add the
  "doors open" annotation when they do.
- During: watch the funnel row; when a tile goes red, the row below it says which device.
- After: set the dashboard to the test's time range and take the panel exports;
  `uv run ennemi-collect` in the `ennemi` repo for the APs' flash logs (the UK Ultra's are RAM
  only, and are now also in Loki); `dnsmasq_log_queries` back to false.

## 10. Open points

1. **Loki as the log store** (§3): agreed, or another store?
2. **Who changes the console** (§4): the remote-logging setting, the lease time and the
   read-only admin can be done in the UI, or by script through the API with a backup first.
3. **Grafana's admin password** moves to `.env`: keep the current one or rotate it.
4. **Adopting the hand-installed Prometheus, Grafana and exporters** into Ansible (§5):
   recommended, so a rebuilt brain gets the whole stack back; the alternative is to add only
   the new pieces and leave the existing ones as they are.
5. **Scope of the webapp change** (§7): server-side metrics only for test 2, client-side
   timings later.

## 11. Status, 2026-11-05 evening (phase 1)

Done and verified on ennemi-brain, deployed role by role with `./deploy ennemi-brain <role>`:

- `prometheus`, `exporters`, `grafana` adopted the hand installs (versions pinned to what was
  there); Prometheus has 9 scrape jobs and a reload line; Grafana 13.2 has the Prometheus and
  Loki datasources and the four dashboards provisioned from `roles/grafana/files/dashboards/`.
- `dnsmasq`: the resolver's config is rendered byte for byte from variables; the exporter
  answers on `127.0.0.1:9153` (`dnsmasq_hits`, `dnsmasq_misses`, `dnsmasq_servers_queries*`).
- `loki` 3.x and `alloy` from `apt.grafana.com`: the journal (12 units) and the nginx JSON log
  (`job="nginx-access"`, labels `status` and `route`) are in Loki; the syslog listener is up on
  UDP 1514 and waiting for the console (§4, not done).
- `unpoller` 5.9.4 polls the console and exports all 9 devices; it also exports
  `unpoller_dhcp_active_leases` and `unpoller_dhcp_pool_size` per network, so the textfile
  script of §5 is not needed.
- nginx: `access.json.log` written, `/api/metrics` answers 404 from outside.
- Webapp: `GET /api/metrics` implemented, tested and built on ennemi-dev (uncommitted), **not
  yet deployed**: `make deploy` in `/home/ennemi/webapp` (restarts the app; no show running).
  Until then the `ennemi_webapp` target is down (401 from the current build).
- Dashboard "Load test — Live" (uid `ennemi-load-test-live`, 54 panels, folder "System
  Monitoring") provisioned; generated by `docs/load-test-live.py` (run it with the JSON path as argument), the JSON is
  the source of truth now. Its UniFi-log panels stay empty until the console sends syslog.

Done later the same evening:

- Webapp deployed (`make deploy` run by hand); the `ennemi_webapp` target is up, the route
  answers 404 from outside.
- Console remote logging set through the API (`set/setting/rsyslogd`; backup of every setting
  in the `ennemi` repo, `data/backup/setting-before-20261105T162539.json`): `ip 192.168.108.2`,
  `port 1514`, contents device + client + admin activity. **With "log to this console"
  (`this_controller`) left on, the console keeps the devices for itself: it saved the server
  but never pushed it to the devices (their config version did not change, even after a
  force-provision).** With `this_controller: false` every device was re-provisioned within a
  minute, points at brain (`syslog.remote.ip=192.168.108.2`, port 1514, unencrypted), and the
  gateway forwards its own syslog-ng stream too, DHCP lines included. The console's own device
  log view is therefore off; the backup restores it.
- What the devices send is plain RFC3164, but with a tag of their own in the TAG field (an AP:
  `mac,model-version`; the gateway: its hostname) and the program at the head of the message.
  Alloy now takes `app` from there (`hostapd`, `stahtd`, `kernel`, `dnsmasq-dhcp`, ...); the
  gateway's firewall lines (`[LAN_LOCAL-RET-...]`, one per packet to the gateway itself, so
  potentially many with a full room) are `app="firewall"`. Seven lines failed to parse during
  the re-provisioning burst, none since.
- The APs' lines come from their Admin addresses, as listed in host_vars; the switch had sent
  nothing in its first half hour (it logs on events only).

Still to do for phase 1:

1. A read-only local admin on the console for unpoller (`UNPOLLER_USER`/`UNPOLLER_PASSWORD`
   in `.env` hold the existing admin login for now).
2. Walk through every panel with a few phones joining; confirm the `ip_delta` units; check the
   switch appears once it has something to say.
3. Commit both repositories.

Later still (after the commits): the resolver's query log had been turned on **by hand on the
host** at 16:41 UTC (`log-queries=extra`, `log-facility=/var/log/dnsmasq/queries.log`,
`log-async=100`, a logrotate entry), and the first deploy of the dnsmasq role from the commit
overwrote it. The role now renders that block (`dnsmasq_log_queries: true` in brain's
host_vars, off again after the tests), owns the logrotate entry as found, and gives Alloy read
access through an ACL; Alloy tails the file into Loki under `unit="dnsmasq"`, the same label
as the journal stream, so the dashboard's dnsmasq panels read both. unpoller's version check
also mis-read its output and reinstalled on every run; fixed. Every role now reports no
change on a second run.
