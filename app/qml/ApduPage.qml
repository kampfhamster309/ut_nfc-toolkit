import QtQuick 2.12
import QtQuick.Layouts 1.12
import Lomiri.Components 1.3

Page {
    id: page
    header: PageHeader { title: "APDU console"; subtitle: root.tag ? ("NFC-" + root.tag.tech + " · " + root.tag.protocol) : "No tag" }

    property var logModel: ListModel {}

    Connections {
        target: root
        function onApduResponse(hex) {
            page.logModel.append({ dir: "<", text: page.group(hex), err: hex.substring(hex.length - 4) !== "9000" && hex.length >= 4 })
        }
    }

    function group(h) {
        h = h.replace(/\s/g, "")
        var out = ""
        for (var i = 0; i < h.length; i += 2) out += h.substr(i, 2) + " "
        return out.trim().toUpperCase()
    }

    function sendApdu(hex) {
        var clean = hex.replace(/\s/g, "")
        if (clean.length < 8 || clean.length % 2) { page.logModel.append({ dir: "!", text: "Need full hex (≥ 4 bytes)", err: true }); return }
        page.logModel.append({ dir: ">", text: group(clean), err: false })
        root.send({ t: "apdu", hex: clean })
    }

    Flickable {
        anchors { left: parent.left; right: parent.right; top: page.header.bottom; bottom: inputRow.top }
        contentHeight: logCol.height; clip: true
        ListView {
            id: logCol
            anchors.fill: parent
            model: page.logModel
            delegate: Item {
                width: logCol.width; height: lbl.height + units.gu(1)
                Label {
                    id: lbl
                    anchors { left: parent.left; right: parent.right; margins: units.gu(2); verticalCenter: parent.verticalCenter }
                    text: (model.dir === ">" ? "→ " : model.dir === "<" ? "← " : "⚠ ") + model.text
                    font.family: "Ubuntu Mono"; wrapMode: Text.WrapAnywhere
                    color: model.dir === ">" ? "#9ecbff" : model.err ? "#ef6b5a" : "#7fd48a"
                }
            }
        }
    }

    // quick presets
    Flow {
        id: presets
        anchors { left: parent.left; right: parent.right; bottom: inputRow.top; margins: units.gu(1) }
        spacing: units.gu(0.8)
        Repeater {
            model: [
                ["SELECT NDEF", "00A4040007D276000085010100"],
                ["SELECT PPSE", "00A404000E325041592E5359532E444446303100"],
                ["GET UID", "FFCA000000"]
            ]
            Button { text: modelData[0]; onClicked: page.sendApdu(modelData[1]) }
        }
    }

    Rectangle {
        id: inputRow
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: rowc.height + units.gu(2); color: "#161922"
        RowLayout {
            id: rowc
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: units.gu(1.5) }
            spacing: units.gu(1)
            TextField {
                id: field; Layout.fillWidth: true
                placeholderText: "APDU hex, e.g. 00A4040007…"
                font.family: "Ubuntu Mono"
                onAccepted: { page.sendApdu(text); text = "" }
            }
            Button { text: "Send"; color: theme.palette.normal.positive
                     onClicked: { page.sendApdu(field.text); field.text = "" } }
        }
    }
}
