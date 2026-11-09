import QtQuick 2.12

Canvas {
    id: icon
    property string symbol: "mic"
    property color color: "#2861dc"
    implicitWidth: 24
    implicitHeight: 24
    onSymbolChanged: requestPaint()
    onColorChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        var c = getContext("2d");
        c.clearRect(0, 0, width, height);
        c.save();
        c.scale(width / 24, height / 24);
        c.strokeStyle = color;
        c.fillStyle = color;
        c.lineWidth = 1.8;
        c.lineCap = "round";
        c.lineJoin = "round";
        function line(x1, y1, x2, y2) {
            c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke();
        }
        if (symbol === "mic") {
            c.beginPath(); c.moveTo(9, 6); c.arc(12, 6, 3, Math.PI, 0);
            c.lineTo(15, 12); c.arc(12, 12, 3, 0, Math.PI); c.closePath(); c.stroke();
            c.beginPath(); c.arc(12, 12, 6, 0, Math.PI); c.stroke();
            line(6, 10, 6, 12); line(18, 10, 18, 12); line(12, 18, 12, 22); line(9, 22, 15, 22);
        } else if (symbol === "sound") {
            c.beginPath(); c.moveTo(3, 9); c.lineTo(7, 9); c.lineTo(12, 5);
            c.lineTo(12, 19); c.lineTo(7, 15); c.lineTo(3, 15); c.closePath(); c.stroke();
            c.beginPath(); c.arc(12, 12, 6, -0.7, 0.7); c.stroke();
            c.beginPath(); c.arc(12, 12, 10, -0.7, 0.7); c.stroke();
        } else if (symbol === "exit") {
            line(10, 4, 4, 4); line(4, 4, 4, 20); line(4, 20, 10, 20);
            line(10, 12, 21, 12); line(17, 8, 21, 12); line(17, 16, 21, 12);
        } else if (symbol === "settings") {
            line(3, 6, 21, 6); line(3, 12, 21, 12); line(3, 18, 21, 18);
            c.clearRect(7, 3, 4, 6); line(9, 3, 9, 9);
            c.clearRect(14, 9, 4, 6); line(16, 9, 16, 15);
            c.clearRect(5, 15, 4, 6); line(7, 15, 7, 21);
        } else if (symbol === "audio") {
            var heights = [6, 12, 18, 10, 14];
            for (var i = 0; i < heights.length; i++) line(4 + i * 4, 12 - heights[i] / 2, 4 + i * 4, 12 + heights[i] / 2);
        } else if (symbol === "chevron") {
            line(6, 9, 12, 15); line(12, 15, 18, 9);
        } else if (symbol === "close") {
            line(6, 6, 18, 18); line(6, 18, 18, 6);
        } else if (symbol === "plus" || symbol === "minus") {
            line(5, 12, 19, 12); if (symbol === "plus") line(12, 5, 12, 19);
        } else if (symbol === "stop") {
            c.strokeRect(6, 6, 12, 12);
        } else if (symbol === "shield") {
            c.beginPath(); c.moveTo(12, 3); c.lineTo(20, 6); c.lineTo(19, 14);
            c.quadraticCurveTo(18, 19, 12, 22); c.quadraticCurveTo(6, 19, 5, 14);
            c.lineTo(4, 6); c.closePath(); c.stroke();
            line(8, 12, 11, 15); line(11, 15, 16, 10);
        } else {
            c.beginPath(); c.moveTo(12, 3); c.lineTo(15, 9); c.lineTo(21, 12);
            c.lineTo(15, 15); c.lineTo(12, 21); c.lineTo(9, 15); c.lineTo(3, 12);
            c.lineTo(9, 9); c.closePath(); c.stroke();
        }
        c.restore();
    }
}
