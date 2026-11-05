#!/usr/bin/env python3
"""Generate the "Load test - Live" Grafana dashboard.

    python3 docs/load-test-live.py roles/grafana/files/dashboards/load-test-live.json

The JSON is what Grafana provisions; this script is how it was first written, kept so a
wholesale change (a datasource uid, a label name) is one edit rather than fifty.
"""
import json
import sys

PROM = {"type": "prometheus", "uid": "PBFA97CFB590B2093"}
LOKI = {"type": "loki", "uid": "loki"}

panels = []
_id = [0]
_y = [0]


def nid():
    _id[0] += 1
    return _id[0]


def row(title):
    panels.append({"id": nid(), "type": "row", "title": title, "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": _y[0]}, "panels": []})
    _y[0] += 1


def target(ds, expr, legend="", ref="A", extra=None):
    t = {"refId": ref, "datasource": ds, "expr": expr}
    if legend:
        t["legendFormat"] = legend
    if ds is LOKI:
        t["queryType"] = "range"
    if extra:
        t.update(extra)
    return t


def panel(kind, title, targets, w, h, x, *, unit=None, thresholds=None, desc=None, stack=False,
          opts=None, overrides=None, decimals=None, minv=None, maxv=None, color=None):
    ds = targets[0]["datasource"]
    defaults = {"color": color or {"mode": "palette-classic"}}
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    if minv is not None:
        defaults["min"] = minv
    if maxv is not None:
        defaults["max"] = maxv
    if thresholds:
        defaults["thresholds"] = {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in thresholds]}
    else:
        defaults["thresholds"] = {"mode": "absolute", "steps": [{"color": "green", "value": None}]}
    if kind == "timeseries":
        defaults["custom"] = {"lineWidth": 1, "fillOpacity": 10 if not stack else 40,
                              "stacking": {"mode": "normal" if stack else "none"},
                              "showPoints": "never", "spanNulls": True,
                              "thresholdsStyle": {"mode": "line" if thresholds else "off"}}
    p = {"id": nid(), "type": kind, "title": title, "datasource": ds, "targets": targets,
         "gridPos": {"h": h, "w": w, "x": x, "y": _y[0]},
         "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
         "options": opts or {}}
    if desc:
        p["description"] = desc
    if kind == "timeseries" and not opts:
        p["options"] = {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}}
    if kind == "stat" and not opts:
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "background", "graphMode": "area", "textMode": "value_and_name",
                        "justifyMode": "center"}
    if kind == "bargauge" and not opts:
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True}
    if kind == "state-timeline" and not opts:
        p["options"] = {"showValue": "auto", "mergeValues": True, "rowHeight": 0.8,
                        "legend": {"showLegend": False}}
    if kind == "logs" and not opts:
        p["options"] = {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                        "dedupStrategy": "none", "enableLogDetails": True}
    panels.append(p)
    return p


def end_row(h):
    _y[0] += h


RED = [(None, "green"), (1, "red")]

# ---------------------------------------------------------------- 1. Funnel
row("Funnel: the path of a phone, left to right. The first tile to go red says which stage to look at")
H = 5
panel("stat", "Clients on Wi-Fi", [target(PROM, "sum(unpoller_device_radio_stations)", "clients")], 3, H, 0,
      desc="Stations associated, all APs and radios (UniFi API).")
panel("stat", "DHCP /min: DISCOVER vs ACK", [
    target(LOKI, 'sum(count_over_time({job="unifi", dhcp_op="DHCPDISCOVER"}[1m]))', "discover", "A"),
    target(LOKI, 'sum(count_over_time({job="unifi", dhcp_op="DHCPACK"}[1m]))', "ack", "B")], 3, H, 3,
      desc="From the gateway's dnsmasq-dhcp log lines. ACK far below DISCOVER: phones asking again and again.")
panel("stat", "DHCP NAK + no address /min", [
    target(LOKI, 'sum(count_over_time({job="unifi", dhcp_op=~"DHCPNAK|NOADDRESS"}[1m]))', "nak+noaddr")], 3, H, 6,
      thresholds=RED, desc="Any value is bad: a NAK sends the phone back to DISCOVER; 'no address available' means the pool is out.")
panel("stat", "Public leases / pool", [
    target(PROM, 'unpoller_dhcp_active_leases{network="Public"}', "leases", "A"),
    target(PROM, 'unpoller_dhcp_pool_size{network="Public"}', "pool", "B")], 3, H, 9,
      desc="Active leases on the audience network against the size of its pool (UniFi API).")
panel("stat", "DNS q/s, upstream failures", [
    target(PROM, "rate(dnsmasq_hits[1m]) + rate(dnsmasq_misses[1m])", "q/s", "A"),
    target(PROM, "sum(rate(dnsmasq_servers_queries_failed[1m]))", "failed/s", "B")], 3, H, 12,
      decimals=1, desc="ennemi-brain's dnsmasq: cache hits + misses per second, and forwarded queries that failed.")
panel("stat", "App sockets", [target(PROM, "ennemi_socket_peers", "{{domain}}")], 3, H, 15,
      desc="WebSockets open on ennemi-webapp, by domain (user = phones).")
panel("stat", "Audience count", [target(PROM, "ennemi_audience_count", "audience")], 2, H, 18,
      desc="Phones the show app counts as present.")
panel("stat", "nginx active conns", [target(PROM, "nginx_connections_active", "active")], 2, H, 20)
panel("stat", "HTTP 5xx + 499 /min", [
    target(LOKI, 'sum(count_over_time({job="nginx-access", status=~"5..|499"}[1m]))', "errors")], 2, H, 22,
      thresholds=RED, desc="Server errors and clients that gave up (499), from nginx's JSON access log.")
end_row(H)

# ---------------------------------------------------------------- 2. Radio
row("Radio, per AP (UniFi API through unpoller, 30 s)")
H = 8
panel("bargauge", "Clients per radio", [target(PROM, "sum by (name, band) (unpoller_device_radio_stations)", "{{name}} {{band}}")],
      6, H, 0, thresholds=[(None, "green"), (40, "orange"), (60, "red")], minv=0, maxv=80,
      desc="More than ~60 on one radio while another is idle: placement or steering.")
panel("timeseries", "Channel utilisation (total)", [
    target(PROM, "unpoller_device_radio_channel_utilization_total_ratio", "{{name}} {{band}}")], 6, H, 6,
      unit="percentunit", minv=0, maxv=1, thresholds=[(None, "green"), (0.6, "orange"), (0.8, "red")],
      desc="Airtime busy as the AP hears it, our own traffic and the neighbours' included.")
panel("timeseries", "Transmit retry rate", [
    target(PROM, "sum by (name, band) (rate(unpoller_device_vap_transmit_retries_total[2m])) / sum by (name, band) (rate(unpoller_device_vap_transmit_packets_total[2m]))", "{{name}} {{band}}")],
      6, H, 12, unit="percentunit", minv=0, thresholds=[(None, "green"), (0.3, "red")],
      desc="Retried frames over transmitted frames per radio, from the VAP counters. Above 30 %: interference or clients too far.")
panel("timeseries", "Average client signal per VAP", [
    target(PROM, 'unpoller_device_vap_average_client_signal{essid="wifi_du_spectacle"} < -1', "{{name}} {{band}}")],
      6, H, 18, unit="dBm", thresholds=[(None, "red"), (-75, "orange"), (-65, "green")],
      desc="Below -75 dBm on average: the phones on that radio are far, or steered to the wrong band.")
end_row(H)
H = 7
panel("state-timeline", "Channel per radio (a colour change is a DFS move)", [
    target(PROM, "unpoller_device_radio_channel", "{{name}} {{band}}")], 12, H, 0,
      color={"mode": "continuous-GrYlRd"})
panel("timeseries", "AP CPU", [target(PROM, 'unpoller_device_cpu_utilization_ratio{type="uap"}', "{{name}}")],
      6, H, 12, unit="percentunit", minv=0, maxv=1, thresholds=[(None, "green"), (0.8, "red")])
panel("timeseries", "Transmit power per radio", [target(PROM, "unpoller_device_radio_transmit_power", "{{name}} {{band}}")],
      6, H, 18, unit="dBm")
end_row(H)

# ---------------------------------------------------------------- 3. Join quality
row("Join quality, from the APs' own logs (stahtd station tracker, hostapd)")
H = 8
panel("timeseries", "Join events /min by type, all APs", [
    target(LOKI, 'sum by (event_type) (count_over_time({job="unifi", app="stahtd", event_type=~".+"}[1m]))', "{{event_type}}")],
      8, H, 0, stack=True, desc="association -> success is the happy path; failure, soft failure and deny are phones that did not get on.")
panel("timeseries", "Failed joins /min per AP", [
    target(LOKI, 'sum by (device) (count_over_time({job="unifi", app="stahtd", event_type=~"failure|soft failure|deny"}[1m]))', "{{device}}")],
      8, H, 8, thresholds=[(None, "green"), (10, "red")])
panel("timeseries", "hostapd 802.11 events /min per AP", [
    target(LOKI, 'sum by (device, hostapd_event) (count_over_time({job="unifi", app="hostapd", hostapd_event=~".+"}[1m]))', "{{device}} {{hostapd_event}}")],
      8, H, 16, stack=True)
end_row(H)
H = 8
panel("timeseries", "Time to an IP, p95 per AP (ip_delta, raw units: confirm against a known join)", [
    target(LOKI, 'quantile_over_time(0.95, {job="unifi", app="stahtd", event_type="success"} | regexp `"ip_delta": "(?P<ip_delta>-?\\d+)"` | unwrap ip_delta [5m]) by (device)', "{{device}}")],
      8, H, 0, desc="The station tracker's ip_delta on successful joins. Looks like microseconds; check before trusting the scale.")
panel("timeseries", "DNS timeouts seen by the APs /min", [
    target(LOKI, 'sum by (device) (count_over_time({job="unifi", app="stahtd"} |~ `"dns_timeouts": "[1-9]` [1m]))', "{{device}}")],
      8, H, 8, thresholds=RED)
panel("logs", "Failed joins (latest)", [
    target(LOKI, '{job="unifi", app="stahtd", event_type=~"failure|soft failure|deny"}')], 8, H, 16)
end_row(H)

# ---------------------------------------------------------------- 4. DHCP & DNS
row("DHCP (gateway log) and DNS (ennemi-brain dnsmasq)")
H = 8
panel("timeseries", "DHCP operations /min", [
    target(LOKI, 'sum by (dhcp_op) (count_over_time({job="unifi", dhcp_op=~".+"}[1m]))', "{{dhcp_op}}")],
      8, H, 0, stack=True,
      overrides=[{"matcher": {"id": "byRegexp", "options": "DHCPNAK|NOADDRESS"},
                  "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}])
panel("timeseries", "Gateway dnsmasq exits", [
    target(LOKI, 'sum(count_over_time({job="unifi", kind="gateway"} |= "process exit event for process dnsmasq" [1m]))', "exits")],
      4, H, 8, thresholds=RED, desc="The gateway's DHCP server restarting mid-show, as on 2026-11-04 at 20:57.")
panel("timeseries", "dnsmasq cache /s", [
    target(PROM, "rate(dnsmasq_hits[1m])", "hits", "A"),
    target(PROM, "rate(dnsmasq_misses[1m])", "misses", "B"),
    target(PROM, "rate(dnsmasq_insertions[1m])", "insertions", "C"),
    target(PROM, "rate(dnsmasq_evictions[1m])", "evictions", "D")], 6, H, 12, decimals=1,
      desc="Evictions above zero: the cache (10000 entries) is too small.")
panel("timeseries", "Upstream queries and failures /s", [
    target(PROM, "rate(dnsmasq_servers_queries[1m])", "{{server}} queries", "A"),
    target(PROM, "rate(dnsmasq_servers_queries_failed[1m])", "{{server}} failed", "B")], 6, H, 18, decimals=1)
end_row(H)
H = 6
panel("timeseries", "dnsmasq 'maximum concurrent queries reached' /min", [
    target(LOKI, 'sum(count_over_time({unit="dnsmasq"} |= "Maximum number of concurrent DNS queries" [1m]))', "hits")],
      8, H, 0, thresholds=RED, desc="dns-forward-max (2000) reached: queries dropped.")
panel("logs", "dnsmasq on ennemi-brain (latest)", [target(LOKI, '{unit="dnsmasq"}')], 16, H, 8)
end_row(H)

# ---------------------------------------------------------------- 5. Server
row("Server: nginx, the host, the show app")
H = 8
panel("timeseries", "nginx connections", [
    target(PROM, "nginx_connections_active", "active", "A"),
    target(PROM, "nginx_connections_reading", "reading", "B"),
    target(PROM, "nginx_connections_writing", "writing", "C"),
    target(PROM, "nginx_connections_waiting", "waiting", "D")], 6, H, 0)
panel("timeseries", "Requests /min by status", [
    target(LOKI, 'sum by (status) (count_over_time({job="nginx-access"}[1m]))', "{{status}}")], 6, H, 6, stack=True)
panel("timeseries", "Requests /min by route", [
    target(LOKI, 'sum by (route) (count_over_time({job="nginx-access"}[1m]))', "{{route}}")], 6, H, 12, stack=True)
panel("timeseries", "Request time p95 by route", [
    target(LOKI, 'quantile_over_time(0.95, {job="nginx-access", route!="websocket"} | json | unwrap request_time [1m]) by (route)', "{{route}}")],
      6, H, 18, unit="s", thresholds=[(None, "green"), (1, "red")])
end_row(H)
H = 8
panel("timeseries", "WebSocket upgrades /min", [
    target(LOKI, 'sum(count_over_time({job="nginx-access", route="websocket", status="101"}[1m]))', "101 upgraded", "A"),
    target(LOKI, 'sum(count_over_time({job="nginx-access", route="websocket", status!="101"}[1m]))', "not upgraded", "B")], 6, H, 0,
      overrides=[{"matcher": {"id": "byName", "options": "not upgraded"},
                  "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}])
panel("timeseries", "Host CPU, softirq, load", [
    target(PROM, '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))', "cpu busy", "A"),
    target(PROM, 'avg(rate(node_cpu_seconds_total{mode="softirq"}[1m]))', "softirq", "B"),
    target(PROM, "node_load1 / count(node_cpu_seconds_total{mode=\"idle\"})", "load1 / cores", "C")], 6, H, 6,
      unit="percentunit", minv=0, thresholds=[(None, "green"), (0.8, "red")])
panel("timeseries", "Conntrack, TCP retransmits, NIC drops", [
    target(PROM, "node_nf_conntrack_entries / node_nf_conntrack_entries_limit", "conntrack used", "A"),
    target(PROM, "rate(node_netstat_Tcp_RetransSegs[1m])", "tcp retrans/s", "B"),
    target(PROM, 'rate(node_network_receive_drop_total{device="enp86s0"}[1m]) + rate(node_network_transmit_drop_total{device="enp86s0"}[1m])', "nic drops/s", "C")],
      6, H, 12, overrides=[{"matcher": {"id": "byName", "options": "conntrack used"},
                            "properties": [{"id": "unit", "value": "percentunit"}, {"id": "custom.axisPlacement", "value": "right"}]}])
panel("timeseries", "App event-loop lag and memory", [
    target(PROM, 'ennemi_eventloop_lag_seconds{quantile="0.99"}', "lag p99", "A"),
    target(PROM, "ennemi_eventloop_lag_max_seconds", "lag max", "B"),
    target(PROM, 'nodejs_heap_used_bytes{job="ennemi_webapp"}', "heap used", "C"),
    target(PROM, 'process_resident_memory_bytes{job="ennemi_webapp"}', "rss", "D")], 6, H, 18, unit="s",
      overrides=[{"matcher": {"id": "byRegexp", "options": "heap used|rss"},
                  "properties": [{"id": "unit", "value": "bytes"}, {"id": "custom.axisPlacement", "value": "right"}]}],
      desc="Lag idles at ~20 ms (the sampler's resolution). Above 100 ms the app is falling behind.")
end_row(H)
H = 8
panel("timeseries", "Socket messages in /s by type", [
    target(PROM, "sum by (type) (rate(ennemi_socket_messages_in_total[1m]))", "{{type}}")], 8, H, 0, stack=True)
panel("timeseries", "Socket messages published and sent /s", [
    target(PROM, "sum(rate(ennemi_socket_messages_published_total[1m]))", "published (per channel)", "A"),
    target(PROM, "sum(rate(ennemi_socket_messages_sent_total[1m]))", "sent (per socket)", "B")], 8, H, 8)
panel("timeseries", "Socket events /min", [
    target(PROM, "sum by (event) (increase(ennemi_socket_events_total[1m]))", "{{event}}")], 8, H, 16, stack=True,
      desc="socket-closed:no-pong and rejoin rising together: phones dropping off the Wi-Fi.",
      overrides=[{"matcher": {"id": "byRegexp", "options": ".*(rejected|refused|error|no-pong|dropped|invalid).*"},
                  "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}])
end_row(H)
H = 6
panel("timeseries", "Dragonfly", [
    target(PROM, "rate(dragonfly_commands_processed_total[1m])", "commands/s", "A"),
    target(PROM, "dragonfly_connected_clients", "clients", "B")], 8, H, 0)
panel("logs", "Show app journal (latest)", [target(LOKI, '{unit="ennemi-webapp"}')], 16, H, 8)
end_row(H)

# ---------------------------------------------------------------- 6. Wired & gateway
row("Wired path and gateway")
H = 7
panel("timeseries", "Switch and gateway port errors + drops /s", [
    target(PROM, "sum by (name, port_name) (rate(unpoller_device_port_receive_errors_total[2m]) + rate(unpoller_device_port_transmit_errors_total[2m]) + rate(unpoller_device_port_receive_dropped_total[2m]) + rate(unpoller_device_port_transmit_dropped_total[2m])) > 0", "{{name}} {{port_name}}")],
      6, H, 0, thresholds=RED, desc="Only ports with a non-zero rate are drawn.")
panel("timeseries", "PoE power per port", [target(PROM, "unpoller_device_port_poe_watts > 0", "{{name}} {{port_name}}")],
      6, H, 6, unit="watt")
panel("state-timeline", "Port speed", [target(PROM, 'unpoller_device_port_port_speed_bps{name="USW Flex 2.5G 8 PoE"} > 0', "{{port_name}}")],
      6, H, 12, unit="bps", color={"mode": "continuous-RdYlGr"})
panel("timeseries", "Gateway CPU, memory", [
    target(PROM, 'unpoller_device_cpu_utilization_ratio{type="udm"}', "cpu", "A"),
    target(PROM, 'unpoller_device_memory_utilization_ratio{type="udm"}', "memory", "B")], 6, H, 18,
      unit="percentunit", minv=0, maxv=1, thresholds=[(None, "green"), (0.7, "red")])
end_row(H)
H = 7
panel("timeseries", "Gateway LAN / WAN throughput", [
    target(PROM, "rate(unpoller_device_lan_receive_bytes_total[1m]) * 8", "lan in", "A"),
    target(PROM, "rate(unpoller_device_lan_transmit_bytes_total[1m]) * 8", "lan out", "B"),
    target(PROM, "unpoller_device_wan_receive_rate_bytes * 8", "wan in", "C"),
    target(PROM, "unpoller_device_wan_transmit_rate_bytes * 8", "wan out", "D")], 8, H, 0, unit="bps")
panel("timeseries", "WAN monitor latency", [target(PROM, "unpoller_device_wan_monitor_latency_average_seconds", "{{target}} {{type}}")],
      8, H, 8, unit="s")
panel("logs", "Gateway log (latest)", [target(LOKI, '{job="unifi", kind="gateway"}')], 8, H, 16)
end_row(H)

dashboard = {
    "uid": "ennemi-load-test-live",
    "title": "Load test — Live",
    "description": "One screen for the night: the audience's path from the radio to the show app, each stage with its own row. ennemi-infra docs/load-test-observability.md.",
    "tags": ["ennemi", "load-test"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "10s",
    "time": {"from": "now-30m", "to": "now"},
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m"]},
    "schemaVersion": 39,
    "version": 1,
    "annotations": {"list": [
        {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
         "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
        {"datasource": LOKI, "enable": True, "iconColor": "purple", "name": "Show events",
         "expr": '{unit="ennemi-webapp"} |~ "(?i)(run started|start instance|stop instance|Play|Stop)"'},
    ]},
    "templating": {"list": []},
    "panels": panels,
}

out = sys.argv[1]
with open(out, "w") as fh:
    json.dump(dashboard, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print(f"{out}: {len(panels)} panels")
