import QtQuick 2.12

Rectangle {
    id: button
    property string label: ""
    property string detail: ""
    property string icon: ""
    property bool primary: false
    property bool selected: false
    property bool flat: false
    property color accent: "#2861dc"
    property color ink: "#17283f"
    property color muted: "#627086"
    property color line: "#dce4ee"
    property color paper: "#ffffff"
    property real textSize: 18
    property real detailSize: 13
    property real inset: 16
    signal clicked()

    implicitWidth: 160
    implicitHeight: 60
    radius: 14
    color: touch.pressed ? "#dce7fb" : (primary ? accent : (selected ? "#e6eeff" : (flat ? "transparent" : paper)))
    border.width: primary || flat ? 0 : 1
    border.color: selected || activeFocus ? accent : line
    opacity: enabled ? 1 : 0.45
    activeFocusOnTab: enabled
    Accessible.role: Accessible.Button
    Accessible.name: label
    Accessible.description: detail
    Accessible.onPressAction: if (enabled) clicked()

    Row {
        anchors.centerIn: parent
        width: Math.max(0, parent.width - 2 * button.inset)
        spacing: button.icon ? 10 : 0
        AppIcon {
            visible: button.icon.length > 0
            symbol: button.icon
            color: button.primary ? "#ffffff" : button.ink
            width: visible ? Math.min(24, button.textSize + 2) : 0
            height: width
            anchors.verticalCenter: parent.verticalCenter
        }
        Column {
            width: Math.max(0, parent.width - (button.icon ? Math.min(24, button.textSize + 2) + parent.spacing : 0))
            spacing: button.detail ? 5 : 0
            Text {
                width: parent.width
                text: button.label
                textFormat: Text.PlainText
                color: button.primary ? "#ffffff" : button.ink
                font.pixelSize: button.textSize
                font.weight: Font.Medium
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
            }
            Text {
                visible: button.detail.length > 0
                width: parent.width
                text: button.detail
                textFormat: Text.PlainText
                color: button.primary ? "#e3ecff" : button.muted
                font.pixelSize: button.detailSize
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
            }
        }
    }
    MouseArea {
        id: touch
        anchors.fill: parent
        onClicked: { button.forceActiveFocus(); button.clicked(); }
    }
    Keys.onReturnPressed: if (enabled) clicked()
    Keys.onEnterPressed: if (enabled) clicked()
    Keys.onSpacePressed: if (enabled) clicked()
}
