#!/usr/bin/env python3
"""
Spielt eine weitere Audiospur der in VLC laufenden Datei auf einem eigenen
Ausgabegeraet ab. Position, Pause, Tempo und Datei kommen per MPRIS von VLC,
die VLC-Web-Schnittstelle wird nicht gebraucht.

  ./companion.py --list
  ./companion.py --track 2 --sink alsa_output.pci-0000_00_1f.3.analog-stereo

Benoetigt: python-vlc, busctl, pactl
"""

import argparse
import json
import subprocess
import sys
import time

try:
    import vlc
except ImportError:
    sys.exit("Fehler: python-vlc fehlt.  Installieren z.B. mit:  yay -S python-vlc")

MPRIS_BUS = "org.mpris.MediaPlayer2.vlc"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_IFACE = "org.mpris.MediaPlayer2.Player"

VLC_GONE_AFTER = 3.0     # s ohne Antwort, bis der Film-VLC als beendet gilt
DRIFT_SOFT_MS = 400      # kleine Abweichung: erst nach mehreren Messungen springen
DRIFT_SOFT_HITS = 3
DRIFT_HARD_MS = 1000     # grosse Abweichung (z.B. Sprung in VLC): sofort springen
SEEK_COOLDOWN = 1.0      # s nach einem Sprung, bis wieder gemessen wird

# WirePlumber merkt sich das Zielgeraet pro application.id. Darum eine eigene
# Kennung je Geraet: sonst wuerde eine Verschiebung auch VLC selbst umleiten.
APP_ID = "org.local.multisink-companion"


def read_vlc(bus):
    """Zustand des Film-VLC per MPRIS, oder None wenn er nicht erreichbar ist."""
    res = subprocess.run(
        ["busctl", "--user", "--json=short", "get-property", bus, MPRIS_PATH, MPRIS_IFACE,
         "PlaybackStatus", "Rate", "Position", "Metadata"],
        capture_output=True, text=True)
    if res.returncode != 0:
        return None
    try:
        status, rate, position, meta = (json.loads(line)["data"]
                                        for line in res.stdout.splitlines())
    except ValueError:
        return None
    return {"status": status, "rate": rate, "pos_ms": position // 1000,
            "url": meta.get("xesam:url", {}).get("data")}


def list_sinks():
    res = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True)
    return [line.split("\t")[1] for line in res.stdout.splitlines() if "\t" in line]


def tracks_of(player):
    out = []
    for tid, name in (player.audio_get_track_description() or []):
        if tid != -1:      # -1 = "Deaktiviert"
            out.append((tid, name.decode(errors="replace") if isinstance(name, bytes) else str(name)))
    return out


def wait_until_tracks(player, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if tracks_of(player):
            return True
        time.sleep(0.1)
    return False


class Companion:
    def __init__(self, args):
        self.track = args.track
        self.sink = args.sink
        self.delay = args.delay
        self.verbose = args.verbose
        self.app_id = f"{APP_ID}.{args.sink}" if args.sink else APP_ID
        self.instance = vlc.Instance("--no-video-title-show", "--quiet")
        self.instance.set_app_id(self.app_id, "1.0", "vlc")
        self.instance.set_user_agent("VLC Companion", "multisink-companion/1.0")
        self.player = self.instance.media_player_new()
        self.player.audio_output_set("pulse")
        self.url = None
        self.paused = False
        self.rate = 1.0
        self.drift_hits = 0
        self.no_seek_until = 0.0

    def load(self, url):
        """Datei oeffnen, Spur setzen und den Strom aufs Zielgeraet legen."""
        self.url = url
        self.paused = False
        self.rate = 1.0
        self.player.set_media(self.instance.media_new(url, ":no-video"))
        if self.sink:
            self.player.audio_output_device_set("pulse", self.sink)
        self.player.play()
        print(f"Datei: {url}", flush=True)
        if self.track is not None and wait_until_tracks(self.player):
            self.player.audio_set_track(self.track)
            time.sleep(0.3)
            active = self.player.audio_get_track()
            print(f"  Spur: id {active} ({dict(tracks_of(self.player)).get(active, '?')})", flush=True)
            if active != self.track:
                print(f"  WARNUNG: Spur {self.track} wurde nicht uebernommen", flush=True)
        if self.sink:
            self.route_to_sink()

    def own_streams(self):
        res = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"],
                             capture_output=True, text=True)
        try:
            streams = json.loads(res.stdout)
        except ValueError:
            return []
        return [str(s["index"]) for s in streams
                if s.get("properties", {}).get("application.id") == self.app_id]

    def route_to_sink(self):
        end = time.time() + 5.0
        streams = self.own_streams()
        while not streams and time.time() < end:
            time.sleep(0.1)
            streams = self.own_streams()
        moved = [s for s in streams
                 if subprocess.run(["pactl", "move-sink-input", s, self.sink]).returncode == 0]
        if moved:
            print(f"  Geraet: {self.sink}", flush=True)
        else:
            print(f"  WARNUNG: Strom nicht auf {self.sink} verschoben", flush=True)

    def follow(self, state):
        """Einen Messwert des Film-VLC uebernehmen."""
        if state["url"] and state["url"] != self.url:
            self.load(state["url"])

        want_pause = state["status"] != "Playing"
        if want_pause != self.paused:
            self.player.set_pause(1 if want_pause else 0)
            self.paused = want_pause
        if self.paused:
            return

        if abs(state["rate"] - self.rate) > 0.01:
            self.player.set_rate(state["rate"])
            self.rate = state["rate"]

        target = state["pos_ms"] + self.delay
        if self.player.get_state() == vlc.State.Ended:
            if target < self.player.get_length() - 2000:   # in VLC zurueckgesprungen
                self.load(self.url)
            return
        own = self.player.get_time()
        if own < 0 or time.monotonic() < self.no_seek_until:
            return

        drift = own - target
        self.drift_hits = self.drift_hits + 1 if abs(drift) > DRIFT_SOFT_MS else 0
        if self.verbose:
            print(f"  vlc={target} eigen={own} abweichung={drift:+d} ms", flush=True)
        if abs(drift) > DRIFT_HARD_MS or self.drift_hits >= DRIFT_SOFT_HITS:
            if self.verbose:
                print("  -> Sprung", flush=True)
            self.player.set_time(max(0, target))
            self.drift_hits = 0
            self.no_seek_until = time.monotonic() + SEEK_COOLDOWN


def do_list(args):
    """Spuren und Geraete anzeigen. Bleibt pausiert, damit nichts zu hoeren ist."""
    state = read_vlc(args.player)
    url = args.file or (state and state["url"])
    if not url:
        sys.exit("Keine Datei angegeben und kein laufender VLC gefunden.")
    instance = vlc.Instance("--no-video-title-show", "--quiet", "--start-paused")
    player = instance.media_player_new()
    media = instance.media_new_path(url) if "://" not in url else instance.media_new_location(url)
    media.add_option(":no-video")
    player.set_media(media)
    player.play()
    wait_until_tracks(player)
    if args.json:
        print(json.dumps([{"id": tid, "name": name} for tid, name in tracks_of(player)]))
        player.stop()
        return
    print(f"\nDatei: {url}\n\nAudiospuren:")
    for tid, name in tracks_of(player):
        print(f"  id {tid:>3}  {name}")
    player.stop()
    print("\nAusgabegeraete:")
    for sink in list_sinks():
        print(f"  {sink}")
    print()


def do_follow(args):
    state = read_vlc(args.player)
    if state is None:
        sys.exit("Kein laufender VLC gefunden. Starte zuerst den Film in VLC.")
    companion = Companion(args)
    last_seen = time.monotonic()
    print("Folge VLC per MPRIS. Beenden mit Strg-C.", flush=True)
    try:
        while True:
            state = read_vlc(args.player)
            if state is None:
                if time.monotonic() - last_seen > VLC_GONE_AFTER:
                    print("VLC nicht mehr erreichbar - beendet.", flush=True)
                    break
            else:
                last_seen = time.monotonic()
                companion.follow(state)
            time.sleep(args.poll)
    except KeyboardInterrupt:
        pass
    finally:
        companion.player.stop()


def main():
    ap = argparse.ArgumentParser(description="Weitere Audiospur synchron zu einem laufenden VLC.")
    ap.add_argument("--list", action="store_true", help="Spuren und Geraete anzeigen und beenden")
    ap.add_argument("--file", help="nur fuer --list: Datei statt der in VLC laufenden")
    ap.add_argument("--json", action="store_true", help="nur fuer --list: Spuren als JSON ausgeben")
    ap.add_argument("--track", type=int, help="Spur-ID (siehe --list)")
    ap.add_argument("--sink", help="Name des Ausgabegeraets (siehe --list)")
    ap.add_argument("--delay", type=int, default=0, help="Versatz in ms (+ = spaeter)")
    ap.add_argument("--poll", type=float, default=0.5, help="Sekunden zwischen den Messungen")
    ap.add_argument("--player", default=MPRIS_BUS, help="MPRIS-Busname des Film-VLC")
    ap.add_argument("--verbose", action="store_true", help="Abweichung bei jeder Messung ausgeben")
    args = ap.parse_args()

    if args.list:
        do_list(args)
    else:
        do_follow(args)


if __name__ == "__main__":
    main()
