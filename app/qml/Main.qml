import QtQuick 2.12
import QtWebSockets 1.1
import QtSystemInfo 5.0
import Lomiri.Components 1.3
import Lomiri.Components.Popups 1.3

MainView {
    id: root
    objectName: "mainView"
    applicationName: "nfctoolkit.local"
    width: units.gu(45)
    height: units.gu(80)
    backgroundColor: "#12141a"
    theme.name: "Lomiri.Components.Themes.SuruDark"

    readonly property string hubUrl: "ws://127.0.0.1:8767"
    readonly property string tokenUrl: "file:///home/phablet/.local/share/nfctoolkit.local/token"

    property string token: ""
    property bool authed: false
    property string lastError: ""
    readonly property bool connected: socket.status === WebSocket.Open && authed

    property bool hasAdapter: false
    property bool powered: false
    property bool emulating: false
    property string btError: ""

    property var tag: null            // current tag info, or null
    property bool hasCopy: false
    property string copyName: ""
    property string flash: ""         // transient status line

    signal apduResponse(string hex)
    signal libraryList(var list)

    function send(obj) {
        if (socket.status === WebSocket.Open) socket.sendTextMessage(JSON.stringify(obj))
    }

    function flashMsg(s) { flash = s; flashTimer.restart() }
    Timer { id: flashTimer; interval: 4000; onTriggered: root.flash = "" }
    Timer { id: goneTimer; interval: 1500; onTriggered: root.tag = null }

    function handle(m) {
        switch (m.t) {
        case "state":
            authed = true; lastError = ""
            hasAdapter = m.adapter; powered = m.powered
            emulating = m.emulating; btError = m.error || ""
            break
        case "tag": tag = m.tag; goneTimer.stop(); break
        case "tag_gone": goneTimer.restart(); break
        case "read_error": flashMsg("Read error: " + m.msg); break
        case "saved": flashMsg("Saved " + m.name); break
        case "copied": hasCopy = true; copyName = m.name; flashMsg("Copied " + m.pages + " pages from " + m.name); break
        case "cloned": flashMsg("Wrote " + m.pages + " pages to the tag"); break
        case "ndef_written": flashMsg("Wrote NDEF (" + m.bytes + " bytes)"); break
        case "apdu_resp": apduResponse(m.hex); break
        case "library": libraryList(m.list); break
        case "error": flashMsg("Error: " + m.msg); break
        }
    }

    function loadToken() {
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            var t = (xhr.responseText || "").trim()
            if (t.length > 0) { token = t; reconnect() }
            else { lastError = "No token yet — is the nfc-toolkit service running?"; tokenRetry.start() }
        }
        xhr.open("GET", tokenUrl); xhr.send()
    }

    function reconnect() {
        if (!token) { loadToken(); return }
        if (socket.active && (socket.status === WebSocket.Open || socket.status === WebSocket.Connecting)) return
        authed = false; socket.active = false; socket.active = true
    }

    WebSocket {
        id: socket
        url: root.hubUrl
        active: false
        onStatusChanged: {
            if (status === WebSocket.Open)
                sendTextMessage(JSON.stringify({ t: "hello", token: root.token,
                                                 visible: Qt.application.state === Qt.ApplicationActive }))
            else if (status === WebSocket.Error || status === WebSocket.Closed) {
                root.authed = false
                if (status === WebSocket.Error) root.lastError = "NFC Toolkit service not reachable"
                retry.restart()
            }
        }
        onTextMessageReceived: root.handle(JSON.parse(message))
    }

    Timer { id: retry; interval: 2000; onTriggered: if (!root.connected) root.reconnect() }
    Timer { id: tokenRetry; interval: 3000; onTriggered: root.loadToken() }

    Connections {
        target: Qt.application
        function onStateChanged() {
            var active = Qt.application.state === Qt.ApplicationActive
            if (active) { root.reconnect(); root.send({ t: "visible", v: true }) }
            else root.send({ t: "visible", v: false })
        }
    }

    ScreenSaver { screenSaverEnabled: Qt.application.state !== Qt.ApplicationActive }

    Component.onCompleted: { loadToken(); pageStack.push(mainPage) }

    PageStack { id: pageStack }

    MainPage { id: mainPage; visible: false }
    Component { id: apduComp; ApduPage {} }
    Component { id: emulateComp; EmulatePage {} }
    Component { id: libraryComp; LibraryPage {} }
    Component { id: ndefComp; NdefWritePage {} }

    function openPage(comp) { pageStack.push(comp) }
}
