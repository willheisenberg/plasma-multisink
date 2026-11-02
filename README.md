<p align="center">
  <img src="assets/banner.png" alt="plasma-multisink: ein Film, zwei Kopfhörer, zwei Sprachen">
</p>

Ersetzt die Audio-Lautstärkeregelung von KDE Plasma durch eine Fassung, in der eine Anwendung auf mehreren Ausgabegeräten gleichzeitig spielen kann. Bei VLC bekommt jedes zusätzliche Gerät auf Wunsch eine eigene Audiospur, zum Beispiel Deutsch auf den einen und Englisch auf den anderen Kopfhörern.

Das Widget ist ein Fork von `applet/` aus [plasma-pa](https://invent.kde.org/plasma/plasma-pa) v6.7.5, sieht also aus und funktioniert wie das Original. Es ist kein offizielles KDE-Projekt.

## Funktionen

Im Reiter „Anwendungen“ hat jede Anwendung, die gerade Ton abspielt, zusätzlich:

- **Mehrere Ausgabegeräte:** Ein Haken pro Gerät legt dieselbe Ausgabe parallel auf dieses Gerät.
- **Eigene VLC-Audiospur pro Gerät:** Jedes weitere Gerät kann eine andere Spur der laufenden Datei abspielen, mit eigener Lautstärke und eigenem Versatz in Millisekunden.
- **Automatische Synchronisierung:** Die zweite Spur folgt VLC bei Position, Pause, Tempo und Dateiwechsel und beendet sich mit VLC.

Die Spur des Hauptgeräts stellst du weiter in VLC selbst ein.

## Voraussetzungen

- KDE Plasma 6 (entwickelt und getestet mit 6.7.5)
- PipeWire mit WirePlumber und `pipewire-pulse` (`pw-dump`, `pw-link`, `pactl`)
- Python 3 und `busctl` (systemd)
- Für eigene Audiospuren: VLC und [python-vlc](https://pypi.org/project/python-vlc/), unter Arch aus dem AUR: `yay -S python-vlc`

## Installation

```sh
git clone https://github.com/willheisenberg/plasma-multisink.git
cd plasma-multisink
kpackagetool6 -t Plasma/Applet -i package
systemctl --user restart plasma-plasmashell
```

Danach in den Einstellungen des Systemabschnitts „Audio-Lautstärke (Multi-Sink)“ einschalten und die originale Audio-Lautstärke ausschalten.

Aktualisieren: `kpackagetool6 -t Plasma/Applet -u package`, danach die Plasma-Shell wie oben neu starten.

## Bekannte Grenzen

- **Nur bei laufendem Ton:** Eine Anwendung erscheint erst in der Liste, wenn sie einen Audiostrom geöffnet hat.
- **Haken werden nicht gespeichert:** Sie gelten nur für den aktuellen Audiostrom. Öffnet eine Anwendung einen neuen Strom, spielt sie wieder nur auf ihrem Hauptgerät. Eigene VLC-Spuren bleiben dagegen bestehen.
- **Kein Versatz bei gleichem Ton:** Geräte mit derselben Ausgabe lassen sich nicht gegeneinander verschieben, Bluetooth läuft also eventuell hörbar nach.
- **Neustart der Shell:** Ein Neustart von Plasma kann laufende zweite Spuren beenden. Sie müssen dann neu gewählt werden.
- **Plasma-Updates:** Das Widget nutzt die interne API `org.kde.plasma.private.volume` und kann mit neuen Plasma-Versionen brechen.
- **Sprache:** Die eigenen Bedienelemente sind fest auf Deutsch.
- **Getestet:** Die parallele Ausgabe wurde bisher nur mit VLC ausprobiert.

## Aufbau

| Pfad | Inhalt |
|---|---|
| `package/contents/code/backend.py` | liest den Audio-Zustand, setzt Verbindungen, startet zweite Spuren |
| `package/contents/code/companion.py` | spielt eine Spur ohne Video ab und folgt VLC per MPRIS |
| `package/contents/ui/MultiSinkRoutes.qml` | Geräte- und Spurauswahl pro Anwendung |

Die übrigen QML-Dateien stammen von KDE. Geändert sind nur `main.qml` (Abschnitt „Multi-Sink“, Stream-Filter und feste Größe) und `ListItemBase.qml` (Kommentar „Multi-Sink“). Bei Plasma-Updates sind das die Stellen zum Abgleichen.

`companion.py` lässt sich auch ohne Widget starten:

```sh
./companion.py --list
./companion.py --track 2 --sink <sink-name>
```

## Lizenz

GPL-3.0-only, siehe [`LICENSE`](LICENSE). Die von KDE übernommenen Dateien behalten ihre SPDX-Angaben, die zugehörigen Lizenztexte liegen in [`LICENSES/`](LICENSES).
