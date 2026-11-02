import QtQuick
import QtQuick.Layouts

import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PC3

// Geraete-Auswahl einer Anwendung; bei VLC zusaetzlich die Spur je Geraet.
ColumnLayout {
    id: routes

    property var app
    spacing: 0

    Repeater {
        model: routes.app ? routes.app.routes : []

        delegate: ColumnLayout {
            id: row

            required property var modelData
            readonly property var tracks: routes.app.tracks

            spacing: 0
            Layout.fillWidth: true

            RowLayout {
                spacing: Kirigami.Units.smallSpacing
                Layout.fillWidth: true

                PC3.CheckBox {
                    text: row.modelData.description
                    checked: row.modelData.mode !== "off"
                    enabled: row.modelData.canDisable
                    Layout.fillWidth: true
                    onToggled: main.msRoute(routes.app.node, row.modelData.sink, checked ? "follow" : "off")
                }

                // Das Hauptgeraet spielt den Ton von VLC selbst, die Spur laesst sich nur dort umstellen
                PC3.Label {
                    visible: row.tracks.length > 0 && row.modelData.primary
                    text: "Spur in VLC wählen"
                    opacity: 0.7
                }

                PC3.ComboBox {
                    id: trackBox
                    visible: row.tracks.length > 0 && row.modelData.mode !== "off" && !row.modelData.primary
                    model: ["Wie in VLC"].concat(row.tracks.map(t => t.name))
                    currentIndex: row.modelData.mode === "track" ? row.tracks.findIndex(t => t.id === row.modelData.track) + 1 : 0
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 10
                    onActivated: index => {
                        if (index === 0) {
                            main.msRoute(routes.app.node, row.modelData.sink, "follow");
                        } else {
                            main.msRoute(routes.app.node, row.modelData.sink, "track", row.tracks[index - 1].id, row.modelData.delay);
                        }
                    }

                    Connections {
                        target: trackBox.popup
                        function onVisibleChanged() {
                            main.msInteracting = trackBox.popup.visible;
                        }
                    }
                }
            }

            RowLayout {
                visible: row.modelData.mode === "track"
                spacing: Kirigami.Units.smallSpacing
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.gridUnit

                PC3.Slider {
                    visible: row.modelData.stream >= 0
                    from: 0
                    to: Math.max(100, row.modelData.volume)
                    stepSize: 1
                    value: row.modelData.volume
                    Layout.fillWidth: true
                    onMoved: main.msSetVolume(row.modelData.stream, value)
                    onPressedChanged: main.msInteracting = pressed
                }

                PC3.Label {
                    visible: row.modelData.stream >= 0
                    text: row.modelData.volume + " %"
                }

                PC3.SpinBox {
                    id: delayBox
                    from: -5000
                    to: 5000
                    stepSize: 50
                    editable: true
                    value: row.modelData.delay
                    onValueModified: {
                        main.msInteracting = true;
                        delayTimer.restart();
                    }

                    PC3.ToolTip.text: "Versatz in ms"
                    PC3.ToolTip.visible: hovered
                    PC3.ToolTip.delay: Kirigami.Units.toolTipDelay

                    // Der Neustart der Companion-Instanz soll erst nach dem letzten Klick kommen
                    Timer {
                        id: delayTimer
                        interval: 800
                        onTriggered: {
                            main.msInteracting = false;
                            main.msRoute(routes.app.node, row.modelData.sink, "track", row.modelData.track, delayBox.value);
                        }
                    }
                }

                PC3.Label {
                    text: "ms"
                }
            }
        }
    }

    PC3.Label {
        visible: main.msError !== "" && routes.app && main.msErrorNode === routes.app.node
        text: main.msError
        color: Kirigami.Theme.negativeTextColor
        wrapMode: Text.Wrap
        Layout.fillWidth: true
    }
}
