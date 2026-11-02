#!/usr/bin/env python3
"""
Backend des Widgets: liest den Audio-Zustand und setzt die Verbindungen.

  backend.py state
  backend.py route --node ID --sink NAME --mode off|follow|track [--track N] [--delay MS]

Modi je Anwendung und Geraet:
  off     keine Ausgabe auf diesem Geraet
  follow  derselbe Ton wie die Anwendung (PipeWire-Verbindung, wie in Helvum)
  track   nur VLC: eigene Audiospur ueber eine Companion-Instanz
"""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import urllib.parse

CODE_DIR = os.path.dirname(os.path.realpath(__file__))
COMPANION = os.path.join(CODE_DIR, "companion.py")
RUNTIME_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "plasma-multisink")
MPRIS_BUS = os.environ.get("MULTISINK_PLAYER", "org.mpris.MediaPlayer2.vlc")
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_IFACE = "org.mpris.MediaPlayer2.Player"
VLC_APP_ID = "org.VideoLAN.VLC"
COMPANION_APP_ID = "org.local.multisink-companion"


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def run_json(*cmd):
    try:
        return json.loads(run(*cmd).stdout or "[]")
    except ValueError:
        return []


def fail(message):
    print(json.dumps({"error": message}, ensure_ascii=False))
    sys.exit(1)


def sink_key(sink):
    return hashlib.sha1(sink.encode()).hexdigest()


# --- PipeWire-Graph ----------------------------------------------------------
class Graph:
    def __init__(self):
        self.nodes = {}
        self.ports = {}
        self.links = []
        for obj in run_json("pw-dump"):
            kind = obj.get("type", "")
            info = obj.get("info") or {}
            props = info.get("props") or {}
            if kind == "PipeWire:Interface:Node":
                self.nodes[obj["id"]] = props
            elif kind == "PipeWire:Interface:Port":
                self.ports[obj["id"]] = {"node": props.get("node.id"),
                                         "direction": info.get("direction"),
                                         "channel": props.get("audio.channel")}
            elif kind == "PipeWire:Interface:Link":
                self.links.append({"id": obj["id"],
                                   "out_node": info.get("output-node-id"),
                                   "out_port": info.get("output-port-id"),
                                   "in_node": info.get("input-node-id"),
                                   "in_port": info.get("input-port-id")})

    def node_id_of_sink(self, sink):
        for node_id, props in self.nodes.items():
            if props.get("node.name") == sink and props.get("media.class") == "Audio/Sink":
                return node_id
        return None

    def ports_of(self, node, direction):
        return [(pid, p["channel"]) for pid, p in self.ports.items()
                if p["node"] == node and p["direction"] == direction]

    def sinks_linked_from(self, node):
        return {l["in_node"] for l in self.links if l["out_node"] == node}

    def port_pairs(self, out_node, in_node):
        """Passende Port-Paare: gleicher Kanal, Mono geht auf alle Eingaenge."""
        outs = self.ports_of(out_node, "output")
        ins = self.ports_of(in_node, "input")
        pairs = [(o, i) for o, oc in outs for i, ic in ins if oc == ic or oc == "MONO"]
        return pairs or list(zip((o for o, _ in outs), (i for i, _ in ins)))


def link(graph, node, sink_node):
    existing = {(l["out_port"], l["in_port"]) for l in graph.links}
    pairs = graph.port_pairs(node, sink_node)
    if not pairs:
        fail("Keine passenden Audio-Anschlüsse gefunden")
    for out_port, in_port in pairs:
        if (out_port, in_port) not in existing:
            res = run("pw-link", str(out_port), str(in_port))
            if res.returncode != 0:
                fail(f"Verbindung fehlgeschlagen: {res.stderr.strip()}")


def unlink(graph, node, sink_node):
    for l in graph.links:
        if l["out_node"] == node and l["in_node"] == sink_node:
            run("pw-link", "-d", str(l["id"]))


# --- Companion-Prozesse ------------------------------------------------------
def companion_file(sink):
    return os.path.join(RUNTIME_DIR, f"companion-{sink_key(sink)}.json")


def alive(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"companion.py" in f.read()
    except OSError:
        return False


def read_companion(path):
    """Eintrag eines laufenden Companions; verwaiste Eintraege werden entfernt."""
    try:
        with open(path) as f:
            entry = json.load(f)
    except (OSError, ValueError):
        return None
    if alive(entry.get("pid")):
        return entry
    os.unlink(path)
    return None


def companions():
    if not os.path.isdir(RUNTIME_DIR):
        return {}
    entries = (read_companion(os.path.join(RUNTIME_DIR, name))
               for name in sorted(os.listdir(RUNTIME_DIR)) if name.startswith("companion-"))
    return {e["sink"]: e for e in entries if e}


def stop_companion(sink):
    path = companion_file(sink)
    entry = read_companion(path)
    if entry:
        os.kill(entry["pid"], signal.SIGTERM)
        os.unlink(path)


def start_companion(sink, track, delay):
    stop_companion(sink)
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    log_path = os.path.join(RUNTIME_DIR, f"log-{sink_key(sink)}.txt")
    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            [sys.executable, COMPANION, "--sink", sink, "--track", str(track),
             "--delay", str(delay), "--player", MPRIS_BUS],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
    with open(companion_file(sink), "w") as f:
        json.dump({"pid": proc.pid, "sink": sink, "track": track, "delay": delay}, f)


# --- VLC ---------------------------------------------------------------------
def vlc_url():
    res = run("busctl", "--user", "--json=short", "get-property", MPRIS_BUS, MPRIS_PATH,
              MPRIS_IFACE, "Metadata")
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)["data"].get("xesam:url", {}).get("data")
    except ValueError:
        return None


def vlc_tracks(url):
    """Spurliste der Datei. Das Oeffnen dauert, darum pro Datei zwischenspeichern."""
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    cache = os.path.join(RUNTIME_DIR, f"tracks-{sink_key(url)}.json")
    try:
        with open(cache) as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    res = run(sys.executable, COMPANION, "--list", "--json", "--file", url)
    try:
        tracks = json.loads(res.stdout)
    except ValueError:
        return []
    if tracks:
        with open(cache, "w") as f:
            json.dump(tracks, f)
    return tracks


# --- Befehle -----------------------------------------------------------------
def percent(volume):
    values = [int(v["value_percent"].rstrip("%")) for v in volume.values()]
    return max(values) if values else 100


def cmd_state(_args):
    graph = Graph()
    sinks = run_json("pactl", "-f", "json", "list", "sinks")
    inputs = run_json("pactl", "-f", "json", "list", "sink-inputs")
    running = companions()
    companion_streams = {s["properties"].get("application.id"): s for s in inputs}

    url = vlc_url()
    tracks = vlc_tracks(url) if url else []
    apps = []
    for stream in inputs:
        props = stream["properties"]
        app_id = props.get("application.id", "")
        if app_id.startswith(COMPANION_APP_ID):
            continue
        try:
            node = int(props.get("object.id"))
        except (TypeError, ValueError):
            continue
        is_vlc = app_id == VLC_APP_ID
        linked = graph.sinks_linked_from(node)
        direct_sinks = [s for s in sinks if graph.node_id_of_sink(s["name"]) in linked]
        direct = len(direct_sinks)
        # Hauptgeraet: das der Anwendung zugewiesene, sonst das erste verbundene
        primary = next((s for s in direct_sinks if s["index"] == stream["sink"]),
                       direct_sinks[0] if direct_sinks else None)
        routes = []
        for sink in sinks:
            name = sink["name"]
            route = {"sink": name, "description": sink["description"],
                     "mode": "off", "track": -1, "delay": 0, "stream": -1, "volume": 100}
            entry = running.get(name) if is_vlc else None
            if entry:
                route.update(mode="track", track=entry["track"], delay=entry["delay"])
                own = companion_streams.get(f"{COMPANION_APP_ID}.{name}")
                if own:
                    route.update(stream=own["index"], volume=percent(own["volume"]))
            elif graph.node_id_of_sink(name) in linked:
                route["mode"] = "follow"
            # Das letzte direkt verbundene Geraet bleibt, sonst waere die App stumm
            route["canDisable"] = route["mode"] != "follow" or direct > 1
            route["primary"] = route["mode"] == "follow" and sink is primary
            routes.append(route)
        title = props.get("media.name", "")
        if is_vlc and url:
            title = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path))
        apps.append({"node": node, "stream": stream["index"],
                     "name": props.get("application.name", "Unbekannt").split(" (LibVLC")[0],
                     "icon": props.get("application.icon_name") or "audio-volume-high",
                     "title": title, "volume": percent(stream["volume"]),
                     "isVlc": is_vlc, "tracks": tracks if is_vlc else [], "routes": routes})
    print(json.dumps({"apps": apps}, ensure_ascii=False, sort_keys=True))


def cmd_route(args):
    graph = Graph()
    sink_node = graph.node_id_of_sink(args.sink)
    if sink_node is None:
        fail(f"Ausgabegerät nicht gefunden: {args.sink}")
    if args.node not in graph.nodes:
        fail("Die Anwendung spielt nicht mehr")

    if args.mode == "follow":
        stop_companion(args.sink)
        link(graph, args.node, sink_node)
        return

    audio_sinks = {n for n, p in graph.nodes.items() if p.get("media.class") == "Audio/Sink"}
    direct = graph.sinks_linked_from(args.node) & audio_sinks
    if sink_node in direct and len(direct) == 1:
        fail("Das letzte direkt verbundene Gerät kann nicht entfernt werden")
    if args.mode == "track" and args.track is None:
        fail("Keine Spur angegeben")

    unlink(graph, args.node, sink_node)
    if args.mode == "track":
        start_companion(args.sink, args.track, args.delay)
    else:
        stop_companion(args.sink)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("state").set_defaults(func=cmd_state)
    route = sub.add_parser("route")
    route.add_argument("--node", type=int, required=True)
    route.add_argument("--sink", required=True)
    route.add_argument("--mode", choices=["off", "follow", "track"], required=True)
    route.add_argument("--track", type=int)
    route.add_argument("--delay", type=int, default=0)
    route.set_defaults(func=cmd_route)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
