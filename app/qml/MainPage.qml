import QtQuick 2.12
import QtQuick.Layouts 1.12
import Lomiri.Components 1.3

Page {
    id: page

    header: PageHeader {
        id: header
        title: "NFC Toolkit"
        subtitle: !root.connected ? "Connecting…"
                  : root.emulating ? "Emulating a tag"
                  : !root.powered ? "NFC idle"
                  : (root.tag ? "Tag present" : "Hold a tag to the back")
        trailingActionBar.actions: [
            Action { iconName: "save"; text: "Library"; enabled: root.connected
                     onTriggered: root.openPage(libraryComp) },
            Action { iconName: "navigation-menu"; text: "APDU console"; enabled: root.connected
                     onTriggered: root.openPage(apduComp) },
            Action { iconName: "broadcast"; text: "Emulate"; enabled: root.connected
                     onTriggered: root.openPage(emulateComp) }
        ]
    }

    // status / problem banner
    Rectangle {
        id: banner
        anchors { left: parent.left; right: parent.right; top: header.bottom }
        height: txt.text !== "" ? txt.implicitHeight + units.gu(2) : 0
        visible: txt.text !== ""
        color: "#2a2117"
        Label {
            id: txt
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: units.gu(2) }
            wrapMode: Text.Wrap; color: "#f5b623"
            text: !root.connected ? (root.lastError || "Connecting to the NFC Toolkit service…")
                  : !root.hasAdapter ? "No NFC adapter."
                  : root.btError
        }
    }

    Flickable {
        anchors { left: parent.left; right: parent.right; top: banner.bottom; bottom: actionBar.top }
        contentHeight: col.height + units.gu(3)
        clip: true

        Column {
            id: col
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: units.gu(2) }
            spacing: units.gu(1.5)

            // emulation indicator
            Rectangle {
                width: parent.width; visible: root.emulating
                height: visible ? emRow.height + units.gu(3) : 0
                radius: units.gu(1); color: "#16233a"; border.color: "#2f7cf6"; border.width: units.dp(1)
                Row {
                    id: emRow; anchors.centerIn: parent; spacing: units.gu(1.5)
                    Icon { name: "broadcast"; width: units.gu(3); height: units.gu(3); color: "#2f7cf6"; anchors.verticalCenter: parent.verticalCenter }
                    Label { text: "Emulating an NDEF tag.\nHold another reader to the phone."; color: "#cfe0ff"; anchors.verticalCenter: parent.verticalCenter }
                }
            }

            // "no tag" hint
            Column {
                width: parent.width; visible: !root.tag && !root.emulating; spacing: units.gu(1)
                topPadding: units.gu(6)
                Icon { name: "nfc"; width: units.gu(8); height: units.gu(8); color: "#3a4150"; anchors.horizontalCenter: parent.horizontalCenter }
                Label { anchors.horizontalCenter: parent.horizontalCenter; text: root.powered ? "Hold a tag to the upper-middle back" : "Waiting for NFC…"; color: "#8a8f99" }
            }

            // tag card
            Rectangle {
                width: parent.width; visible: root.tag !== null
                height: visible ? card.height + units.gu(3) : 0
                radius: units.gu(1); color: "#1a1d26"
                Column {
                    id: card
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: units.gu(2) }
                    spacing: units.gu(0.8)
                    RowLayout {
                        width: parent.width
                        Label { text: root.tag ? (root.tag.model || root.tag.type) : ""; font.weight: Font.Medium; textSize: Label.Large; color: "#fff"; Layout.fillWidth: true; elide: Text.ElideRight }
                        Rectangle { visible: root.tag && root.tag.cloneable; radius: units.gu(0.4); color: "#1f3a24"
                            implicitWidth: cl.width + units.gu(1.5); implicitHeight: cl.height + units.gu(0.8)
                            Label { id: cl; anchors.centerIn: parent; text: "writable"; textSize: Label.Small; color: "#7fd48a" } }
                    }
                    Repeater {
                        model: root.tag ? [
                            ["UID", root.tag.uid],
                            ["Technology", "NFC-" + root.tag.tech],
                            ["Protocol", root.tag.protocol],
                            ["SAK", root.tag.sak !== null && root.tag.sak !== undefined ? ("0x" + root.tag.sak.toString(16)) : "–"],
                            ["Interfaces", (root.tag.interfaces || []).join(", ").replace(/org.sailfishos.nfc./g, "")]
                        ] : []
                        RowLayout {
                            width: card.width; spacing: units.gu(1)
                            Label { text: modelData[0]; color: "#8a8f99"; Layout.preferredWidth: units.gu(12) }
                            Label { text: modelData[1]; color: "#dfe3ea"; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere }
                        }
                    }
                    // NDEF records
                    Repeater {
                        model: root.tag ? root.tag.ndef : []
                        Column {
                            width: card.width; topPadding: units.gu(0.6)
                            Label { text: "NDEF: " + (modelData.text ? modelData.text : ("type " + modelData.type)); color: "#9ecbff"; wrapMode: Text.WrapAnywhere; width: parent.width }
                        }
                    }
                    // Type 2 dump preview
                    Label {
                        visible: root.tag && root.tag.t2 ? true : false
                        width: card.width
                        text: root.tag && root.tag.t2 ? (root.tag.t2.n_pages + " pages · " + root.tag.t2.dump_hex.substring(0, 32) + "…") : ""
                        color: "#6a7080"; font.family: "Ubuntu Mono"; textSize: Label.Small
                    }
                }
            }

            Label {
                width: parent.width; visible: root.flash !== ""
                text: root.flash; color: "#7fd48a"; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter
            }
            Label {
                width: parent.width; visible: root.hasCopy
                text: "Clone source ready: " + root.copyName + ". Hold a blank tag and tap Clone."
                color: "#9ecbff"; wrapMode: Text.Wrap; textSize: Label.Small; horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    // bottom action bar
    Rectangle {
        id: actionBar
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: root.tag ? grid.height + units.gu(2) : 0
        visible: root.tag !== null && !root.emulating
        color: "#161922"
        GridLayout {
            id: grid
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: units.gu(1.5) }
            columns: 2; columnSpacing: units.gu(1.5); rowSpacing: units.gu(1)
            Button { Layout.fillWidth: true; text: "Save (.nfc)"; onClicked: root.send({ t: "save" }) }
            Button { Layout.fillWidth: true; text: "Copy"; enabled: root.tag && root.tag.cloneable; onClicked: root.send({ t: "copy" }) }
            Button { Layout.fillWidth: true; text: "Clone →"; enabled: root.tag && root.tag.cloneable && root.hasCopy
                     color: theme.palette.normal.positive; onClicked: root.send({ t: "clone" }) }
            Button { Layout.fillWidth: true; text: "Write NDEF"; enabled: root.tag && root.tag.cloneable; onClicked: root.openPage(ndefComp) }
        }
    }
}
