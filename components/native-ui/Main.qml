import QtQuick 2.12
import QtQuick.Window 2.12
import "Http.js" as Http

Window {
    id: app
    title: "RK3588 离线 AI 助手"
    width: 720
    height: 1280
    color: background
    visible: !checkMode

    property bool checkMode: false
    property bool compact: height < 800 || width > height
    property real unit: Math.max(0.85, Math.min(width / 520, height / 920, 1.45))
    property real pad: Math.round((compact ? 18 : 26) * unit)
    property real gap: Math.round((compact ? 10 : 18) * unit)
    property color background: "#f3f6fb"
    property color paper: "#ffffff"
    property color ink: "#17283f"
    property color secondary: "#627086"
    property color accent: "#2861dc"
    property color hairline: "#dce4ee"
    property bool settingsOpen: false
    property bool exitConfirm: false
    property bool showDiagnostics: false
    property bool conversationPending: false
    property bool conversationAvailable: false
    property int snapshotRevision: 0
    property string phaseLabel: ""
    property string phaseHint: ""
    property string phase: "stopped"
    property string conversationWarning: ""
    property string snapshotError: ""
    property string actionError: ""

    property string selectedModel: "qwen2-0.5b"
    property var installedModels: []
    property bool serviceOnline: false
    property bool voiceRunning: false
    property string runningModel: ""
    property bool changingVoice: false
    property bool statusPending: false
    property bool voicePollPending: false
    property bool controlPending: false
    property int voicePid: 0
    property int controlRevision: 0
    property bool desktopWakePending: false
    property string desktopWakeToken: ""
    property bool exiting: false
    property bool volumePollPending: false
    property bool volumeAdjustPending: false
    property int volumeRevision: 0
    property bool volumeReady: false
    property int volumePercent: 0
    property string volumeRaw: ""
    property string volumeHint: "读取音量中…"
    property int speaker: 2
    property var speeds: [1.0, 1.15, 1.25, 1.35]
    property int speedIndex: 2
    property string notice: "连接本地服务中……"
    property string lastLog: "识别到的问题、模型回答和耗时会显示在这里。"

    function modelLabel(name) {
        return name === "deepseek-r1-1.5b" ? "DeepSeek-R1 · 1.5B" : "Qwen2 · 0.5B";
    }

    function statusTitle() {
        if (exiting) return "正在返回桌面";
        if (changingVoice) return voiceRunning ? "正在停止对话" : "正在启动对话";
        if (!serviceOnline) return "本地服务未就绪";
        if (!voiceRunning) return "对话已停止";
        if (snapshotError) return "语音运行中";
        return conversationAvailable && phaseLabel ? phaseLabel : "语音运行中";
    }

    function statusHint() {
        if (exiting || changingVoice || !serviceOnline) return notice;
        if (actionError) return actionError;
        if (!voiceRunning) return "点击开始后，说“你好小助手”唤醒";
        if (snapshotError) return "会话显示暂不可用，语音功能可继续使用";
        if (conversationWarning) return conversationWarning;
        if (phase === "waiting") return "说“你好小助手”，听到提示音后提问";
        if (phase === "loading") return "正在加载本机语音资源，请稍候";
        if (phase === "listening") return "请说出问题，说完稍停片刻";
        if (phase === "thinking") return "正在本地生成回答，请稍候";
        if (phase === "answering" || phase === "preparing") return "正式回答会分段播报";
        return notice;
    }

    function applyConversation(data) {
        if (!data || data.schema_version !== 1 || data.source !== "log-derived") return;
        if (voiceRunning && (data.pid !== voicePid || !data.running)) return;
        conversationAvailable = true;
        snapshotError = "";
        phase = data.phase || "running";
        phaseLabel = data.phase_label || "语音运行中";
        conversationWarning = data.warning || "";
        if (!voiceRunning || !data.turn_id || !data.question) return;
        var follow = chatView.atYEnd || messages.count < 2;
        var key = String(data.turn_id);
        var userIndex = -1;
        var answerIndex = -1;
        for (var i = 0; i < messages.count; i++) {
            var item = messages.get(i);
            if (item.turn === key) {
                if (item.who === "user") userIndex = i;
                else answerIndex = i;
            }
        }
        if (userIndex < 0) messages.append({"who":"user", "content":data.question,
                                            "turn":key, "partial":false});
        if (data.answer) {
            if (answerIndex < 0) messages.append({"who":"assistant", "content":data.answer,
                                                 "turn":key, "partial":!!data.answer_partial});
            else {
                messages.setProperty(answerIndex, "content", data.answer);
                messages.setProperty(answerIndex, "partial", !!data.answer_partial);
            }
        }
        while (messages.count > 40) messages.remove(0);
        if (follow) chatScroll.restart();
    }

    function refreshConversation() {
        if (checkMode || conversationPending || exiting) return;
        conversationPending = true;
        var revision = snapshotRevision;
        Http.request(app, "GET", "/api/ui/conversation", null, function(error, data) {
            conversationPending = false;
            if (exiting || revision !== snapshotRevision) return;
            if (error) { snapshotError = error; return; }
            applyConversation(data);
        }, 3500, "http://127.0.0.1:8082");
    }

    function makeTimeout(milliseconds, callback) {
        var timer = timeoutFactory.createObject(app, {
            "interval": milliseconds, "action": callback
        });
        timer.start();
        return timer;
    }

    function refreshService() {
        if (statusPending || exiting) return;
        statusPending = true;
        Http.request(app, "GET", "/api/status", null, function(error, data) {
            statusPending = false;
            serviceOnline = !error && !!data.online;
            if (serviceOnline) {
                installedModels = data.models || [];
                if (notice === "连接本地服务中……" || notice.indexOf("服务不可用") === 0)
                    notice = "点击“开始对话”后，先说“你好小助手”，听到提示音再提问。";
            } else {
                notice = "服务不可用：" + (error || "RKLLama 尚未就绪");
            }
        }, 5000);
    }

    function refreshVoice() {
        if (voicePollPending || exiting) return;
        voicePollPending = true;
        Http.request(app, "GET", "/api/voice/status", null, function(error, data) {
            voicePollPending = false;
            if (error) return;
            voiceRunning = !!data.running;
            if (data.running && data.pid && data.pid !== voicePid) {
                voicePid = data.pid;
                controlRevision = 0;
                snapshotRevision += 1;
                conversationAvailable = false;
                phaseLabel = "";
                conversationWarning = "";
            }
            if (!voiceRunning) runningModel = "";
            else if (controlRevision === 0) runningModel = data.model || "";
            if (data.last_log) lastLog = data.last_log;
            refreshConversation();
        }, 4000);
    }

    function applyVoiceControl(data) {
        if (!data || app.voicePid <= 0 || data.pid !== app.voicePid
                || typeof data.revision !== "number" || data.revision <= app.controlRevision) return;
        app.controlRevision = data.revision;
        if (app.installedModels.indexOf(data.model) >= 0) {
            app.selectedModel = data.model;
            if (app.voiceRunning) app.runningModel = data.model;
        }
        if (typeof data.speaker === "number" && data.speaker >= 0 && data.speaker <= 4
                && Math.floor(data.speaker) === data.speaker) app.speaker = data.speaker;
        if (typeof data.speed === "number" && data.speed >= 0.5 && data.speed <= 2.0) {
            var choices = app.speeds.slice(0);
            if (choices.indexOf(data.speed) < 0) {
                choices.push(data.speed);
                choices.sort(function(a, b) { return a - b; });
                app.speeds = choices;
            }
            app.speedIndex = app.speeds.indexOf(data.speed);
        }
        if (data.feedback) app.notice = data.feedback;
        if (data.exit_requested === true) app.requestExit();
    }

    function refreshVoiceControl() {
        if (controlPending || exiting) return;
        controlPending = true;
        Http.request(app, "GET", "/api/voice/control", null, function(error, data) {
            controlPending = false;
            if (!error && !exiting) applyVoiceControl(data);
        }, 4000, "http://127.0.0.1:8082");
    }

    function acceptDesktopWake(data) {
        if (!data || data.status !== "pending" || typeof data.token !== "string"
                || data.token.length !== 32 || data.token === app.desktopWakeToken
                || !app.serviceOnline || app.voiceRunning || app.changingVoice || app.exiting) return;
        if (app.installedModels.indexOf(data.model) < 0) return;
        app.desktopWakeToken = data.token;
        app.selectedModel = data.model;
        if (typeof data.speaker === "number" && data.speaker >= 0 && data.speaker <= 4
                && Math.floor(data.speaker) === data.speaker) app.speaker = data.speaker;
        if (typeof data.speed === "number" && data.speed >= 0.5 && data.speed <= 2) {
            var choices = app.speeds.slice(0);
            if (choices.indexOf(data.speed) < 0) {
                choices.push(data.speed);
                choices.sort(function(a, b) { return a - b; });
                app.speeds = choices;
            }
            app.speedIndex = app.speeds.indexOf(data.speed);
        }
        app.showFullScreen();
        if (typeof app.requestActivate === "function") app.requestActivate();
        app.startVoice();
    }

    function refreshDesktopWake() {
        if (desktopWakePending || exiting) return;
        desktopWakePending = true;
        Http.request(app, "GET", "/api/desktop-wake", null, function(error, data) {
            desktopWakePending = false;
            if (!error && !exiting) acceptDesktopWake(data);
        }, 4000, "http://127.0.0.1:8082");
    }

    function selectModel(name) {
        if (voiceRunning || changingVoice || exiting) return;
        selectedModel = name;
    }

    function receiveVolume(error, data) {
        volumeReady = !error;
        if (error) {
            volumeHint = "音量控制未就绪";
            return;
        }
        volumePercent = data.percent;
        volumeRaw = data.values.join("/");
        volumeHint = data.origin === "systemui" ? "与原厂系统音量同步"
                   : (data.key_device ? "实体 V+ / V− 已连接" : "屏幕调节可用；实体键等待连接");
    }

    function refreshVolume() {
        if (volumePollPending || volumeAdjustPending || exiting) return;
        volumePollPending = true;
        var revision = volumeRevision;
        Http.request(app, "GET", "/api/volume", null, function(error, data) {
            volumePollPending = false;
            // A click may have changed volume while this background read
            // was in flight. Do not let its old value overwrite the result.
            if (exiting || revision !== volumeRevision) return;
            receiveVolume(error, data);
        }, 5000, "http://127.0.0.1:8082");
    }

    function adjustVolume(direction) {
        if (!volumeReady || volumeAdjustPending || exiting) return;
        volumeAdjustPending = true;
        volumeRevision += 1;
        Http.request(app, "POST", "/api/volume", {"step": direction}, function(error, data) {
            volumeAdjustPending = false;
            if (exiting) return;
            receiveVolume(error, data);
            if (error) notice = "调节音量失败：" + error;
        }, 7000, "http://127.0.0.1:8082");
    }

    function startVoice() {
        if (!serviceOnline || changingVoice || voiceRunning || exiting) return;
        if (installedModels.indexOf(selectedModel) < 0) {
            notice = "RKLLama 没有找到所选模型，请检查模型目录。";
            actionError = notice;
            return;
        }
        changingVoice = true;
        actionError = "";
        snapshotRevision += 1;
        conversationAvailable = false;
        phaseLabel = "";
        conversationWarning = "";
        notice = "正在启动，加载语音模型需要几秒钟……";
        Http.request(app, "POST", "/api/voice/start", {
            "model": selectedModel, "speaker": speaker,
            "speed": speeds[speedIndex], "max_speech_chars": 1000
        }, function(error, data) {
            changingVoice = false;
            if (exiting) return;
            if (error) {
                notice = "启动失败：" + error;
                actionError = notice;
            } else {
                voiceRunning = true;
                voicePid = data.pid || 0;
                controlRevision = 0;
                runningModel = selectedModel;
                notice = "等待日志显示“等待唤醒词”，再说“你好小助手”。";
            }
            refreshVoice();
        }, 12000);
    }

    function stopVoice() {
        if (changingVoice || exiting) return;
        changingVoice = true;
        actionError = "";
        snapshotRevision += 1;
        notice = "正在停止语音对话……";
        Http.request(app, "POST", "/api/voice/stop", {}, function(error, data) {
            changingVoice = false;
            if (exiting) return;
            if (error) { notice = "停止失败：" + error; actionError = notice; }
            else {
                voiceRunning = false;
                notice = "语音对话已停止，可以切换模型。";
            }
            refreshVoice();
        }, 6000);
    }

    function requestExit() {
        if (exiting) return;
        exiting = true;
        notice = "正在停止监听并退出……";
        console.log("APP_EXIT_REQUEST");
        // Exit stays bounded even if the local service is unavailable.
        Http.request(app, "POST", "/api/voice/stop", {}, function(error, data) {
            if (error) console.warn("APP_EXIT_VOICE_STOP: " + error);
            console.log("APP_EXIT_DONE");
            Qt.quit();
        }, 5000);
    }

    onClosing: {
        close.accepted = checkMode;
        if (!checkMode) requestExit();
    }
    onActiveChanged: console.log("APP_FOCUS active=" + active)
    onSceneGraphError: console.error("APP_RENDER_ERROR: " + message)
    Component.onCompleted: {
        if (checkMode) { uiCheckTimer.start(); return; }
        app.showFullScreen();
        console.log("APP_READY version=0.8.0 voice-controls=0.7.0 layout=conversation size=" + width + "x" + height);
        refreshService();
        refreshVoice();
        refreshVolume();
        refreshDesktopWake();
    }

    Component {
        id: timeoutFactory
        Timer {
            property var action
            repeat: false
            onTriggered: if (action) action()
        }
    }
    Timer {
        interval: 5000
        running: !app.exiting && !app.checkMode
        repeat: true
        onTriggered: app.refreshService()
    }
    Timer {
        interval: 1500
        running: !app.exiting && !app.checkMode
        repeat: true
        onTriggered: {
            app.refreshVoice();
            app.refreshVoiceControl();
            app.refreshDesktopWake();
        }
    }
    Timer {
        interval: 500
        running: !app.exiting && !app.checkMode
        repeat: true
        onTriggered: app.refreshVolume()
    }

    ListModel { id: messages }
    Timer { id: chatScroll; interval: 60; onTriggered: chatView.positionViewAtEnd() }
    Timer {
        id: uiCheckTimer
        objectName: "uiCheckTimer"
        interval: 350
        onTriggered: {
            if (mainArea.width < 100 || chatZone.height < 50 || settingsSheet.width < 100
                    || startButton.height < 40 || exitButton.height < 40) {
                console.error("UI_QML_CHECK_FAIL invalid layout");
            } else {
                console.log("UI_QML_CHECK_OK v0.8.0 size=" + app.width + "x" + app.height);
            }
            Qt.quit();
        }
    }

    Item {
        id: mainArea
        anchors.fill: parent
        anchors.margins: app.pad
        enabled: !app.settingsOpen && !app.exitConfirm
        focus: true
        Keys.onEscapePressed: app.exitConfirm = true

        Item {
            id: header
            anchors { top: parent.top; left: parent.left; right: parent.right }
            height: (app.compact ? 48 : 54) * app.unit
            Rectangle {
                id: brand
                width: 46 * app.unit; height: width
                anchors.verticalCenter: parent.verticalCenter
                radius: 14 * app.unit; color: app.accent
                AppIcon { anchors.centerIn: parent; width: parent.width * 0.55; height: width; symbol: "audio"; color: "#ffffff" }
            }
            Column {
                anchors { left: brand.right; leftMargin: 12 * app.unit; verticalCenter: parent.verticalCenter }
                Text { text: "小助手"; color: app.ink; font.pixelSize: 26 * app.unit; font.weight: Font.DemiBold }
                Text { text: "RK3588 · 离线语音助手"; color: app.secondary; font.pixelSize: 11 * app.unit }
            }
            TouchButton {
                id: exitButton
                objectName: "exitButton"
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                width: 90 * app.unit; height: Math.max(44, 46 * app.unit)
                label: app.exiting ? "退出中" : "退出"; icon: "exit"; flat: true
                textSize: 15 * app.unit; inset: 8 * app.unit
                enabled: !app.exiting
                onClicked: app.exitConfirm = true
            }
        }

        Item {
            id: readiness
            anchors { left: parent.left; right: parent.right; top: header.bottom; topMargin: app.gap }
            height: 20 * app.unit
            Row {
                spacing: 7 * app.unit
                anchors.verticalCenter: parent.verticalCenter
                Rectangle { width: 6 * app.unit; height: width; radius: width / 2; color: app.serviceOnline ? "#24775b" : "#a35c18"; anchors.verticalCenter: parent.verticalCenter }
                Text { text: app.serviceOnline ? "本地服务已就绪" : "正在连接本地服务"; color: app.serviceOnline ? "#24775b" : "#a35c18"; font.pixelSize: 12 * app.unit }
            }
            Row {
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                spacing: 5 * app.unit
                AppIcon { symbol: "shield"; width: 15 * app.unit; height: width; color: app.secondary }
                Text { text: "无需联网"; color: app.secondary; font.pixelSize: 12 * app.unit }
            }
        }

        Item {
            id: toolbar
            anchors { left: parent.left; right: parent.right; top: readiness.bottom; topMargin: app.gap }
            height: (app.compact ? 54 : 72) * app.unit
            Rectangle {
                id: modelCard
                anchors { left: parent.left; right: settingsButton.left; rightMargin: 12 * app.unit; top: parent.top; bottom: parent.bottom }
                radius: 16 * app.unit; color: app.paper; border.color: app.hairline
                Accessible.role: Accessible.Button
                Accessible.name: "当前模型 " + app.modelLabel(app.selectedModel) + "，点击打开设置"
                Accessible.onPressAction: app.settingsOpen = true
                Column {
                    anchors { left: parent.left; leftMargin: 16 * app.unit; right: downArrow.left; rightMargin: 8 * app.unit; verticalCenter: parent.verticalCenter }
                    spacing: 4 * app.unit
                    Text { visible: !app.compact; text: "当前模型"; color: app.secondary; font.pixelSize: 11 * app.unit }
                    Text { width: parent.width; text: app.modelLabel(app.selectedModel); color: app.ink; font.pixelSize: 16 * app.unit; font.weight: Font.Medium; wrapMode: Text.Wrap }
                }
                AppIcon { id: downArrow; anchors { right: parent.right; rightMargin: 14 * app.unit; verticalCenter: parent.verticalCenter } width: 17 * app.unit; height: width; symbol: "chevron"; color: app.secondary }
                MouseArea { anchors.fill: parent; onClicked: app.settingsOpen = true }
            }
            TouchButton {
                id: settingsButton
                anchors { right: parent.right; top: parent.top; bottom: parent.bottom }
                width: 108 * app.unit; label: "设置"; icon: "settings"
                textSize: 16 * app.unit; inset: 12 * app.unit
                onClicked: app.settingsOpen = true
            }
        }

        Item {
            id: chatZone
            objectName: "chatZone"
            anchors { left: parent.left; right: parent.right; top: toolbar.bottom; topMargin: app.gap; bottom: sessionCard.top; bottomMargin: app.gap }
            Row {
                id: chatHeading
                height: 24 * app.unit
                anchors { left: parent.left; right: parent.right; top: parent.top }
                Text { text: "本次对话"; color: app.ink; font.pixelSize: 13 * app.unit; font.weight: Font.Medium }
            }
            Text {
                anchors { right: parent.right; top: parent.top }
                text: "本机处理"; color: app.secondary; font.pixelSize: 11 * app.unit
            }
            ListView {
                id: chatView
                objectName: "conversationList"
                anchors { left: parent.left; right: parent.right; top: chatHeading.bottom; topMargin: 10 * app.unit; bottom: parent.bottom }
                model: messages
                clip: true
                spacing: 18 * app.unit
                boundsBehavior: Flickable.StopAtBounds
                delegate: Item {
                    id: messageRow
                    width: chatView.width
                    height: author.height + 8 * app.unit + bubble.height
                    property bool fromUser: who === "user"
                    Text {
                        id: author
                        anchors { top: parent.top; right: messageRow.fromUser ? parent.right : undefined; left: messageRow.fromUser ? undefined : parent.left }
                        text: messageRow.fromUser ? "你" : (partial ? "小助手 · 回答片段" : "小助手")
                        color: app.secondary; font.pixelSize: 12 * app.unit
                    }
                    Rectangle {
                        id: bubble
                        anchors { top: author.bottom; topMargin: 8 * app.unit; right: messageRow.fromUser ? parent.right : undefined; left: messageRow.fromUser ? undefined : parent.left }
                        width: messageRow.fromUser ? parent.width * 0.88 : parent.width * 0.96
                        height: bubbleText.implicitHeight + 30 * app.unit
                        radius: 17 * app.unit
                        color: messageRow.fromUser ? "#e6eeff" : app.paper
                        border.width: messageRow.fromUser ? 0 : 1; border.color: app.hairline
                        Text {
                            id: bubbleText
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 15 * app.unit }
                            text: content; textFormat: Text.PlainText
                            color: app.ink; font.pixelSize: (messageRow.fromUser ? 17 : 18) * app.unit
                            wrapMode: Text.Wrap; lineHeight: 1.45
                        }
                    }
                }
            }
            Column {
                visible: messages.count === 0
                anchors.centerIn: parent
                width: parent.width * 0.9
                spacing: 15 * app.unit
                AppIcon { anchors.horizontalCenter: parent.horizontalCenter; symbol: "sparkles"; width: 34 * app.unit; height: width; color: app.accent }
                Text { width: parent.width; text: "你好，有什么想聊的？"; color: app.ink; font.pixelSize: 21 * app.unit; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap }
                Text { width: parent.width; text: "开始对话后，你说的话和我的回答\n会显示在这里。"; color: app.secondary; font.pixelSize: 13 * app.unit; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; lineHeight: 1.5 }
            }
        }

        Rectangle {
            id: sessionCard
            anchors { left: parent.left; right: parent.right; bottom: startButton.top; bottomMargin: app.gap }
            height: (app.compact ? 62 : 78) * app.unit
            color: app.paper; radius: 16 * app.unit; border.color: app.hairline
            Rectangle {
                id: stateMark
                width: 38 * app.unit; height: width; radius: 12 * app.unit
                anchors { left: parent.left; leftMargin: 14 * app.unit; verticalCenter: parent.verticalCenter }
                color: app.voiceRunning ? "#e6eeff" : "#eaf0f7"
                AppIcon { anchors.centerIn: parent; symbol: "mic"; width: 22 * app.unit; height: width; color: app.voiceRunning ? app.accent : app.secondary }
            }
            Column {
                anchors { left: stateMark.right; leftMargin: 12 * app.unit; right: parent.right; rightMargin: 14 * app.unit; verticalCenter: parent.verticalCenter }
                spacing: 4 * app.unit
                Text { width: parent.width; text: app.statusTitle(); color: app.ink; font.pixelSize: 16 * app.unit; font.weight: Font.Medium; wrapMode: Text.Wrap }
                Text { width: parent.width; text: app.statusHint(); color: app.secondary; font.pixelSize: 11.5 * app.unit; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight }
            }
        }

        TouchButton {
            id: startButton
            objectName: "startStopButton"
            anchors { left: parent.left; right: parent.right; bottom: voiceSummary.top; bottomMargin: 8 * app.unit }
            height: Math.max(48, (app.compact ? 52 : 60) * app.unit)
            primary: !app.voiceRunning
            icon: app.voiceRunning ? "stop" : "mic"
            label: app.changingVoice ? "处理中…" : (app.voiceRunning ? "停止对话" : "开始对话")
            textSize: 20 * app.unit
            enabled: (app.serviceOnline || app.voiceRunning) && !app.changingVoice && !app.exiting
            onClicked: app.voiceRunning ? app.stopVoice() : app.startVoice()
        }
        Item {
            id: voiceSummary
            anchors { left: parent.left; right: parent.right; bottom: volumeZone.top; bottomMargin: app.gap }
            height: 18 * app.unit
            Text { text: "音色 " + app.speaker + " · 语速 " + app.speeds[app.speedIndex] + "×"; color: app.secondary; font.pixelSize: 11 * app.unit }
            Text { anchors.right: parent.right; text: "听到提示音后提问"; color: app.secondary; font.pixelSize: 11 * app.unit }
        }
        Item {
            id: volumeZone
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: (app.compact ? 70 : 96) * app.unit
            Rectangle { anchors { top: parent.top; left: parent.left; right: parent.right } height: 1; color: app.hairline }
            Row {
                anchors { left: parent.left; top: parent.top; topMargin: (app.compact ? 8 : 14) * app.unit }
                spacing: 7 * app.unit
                AppIcon { symbol: "sound"; width: 17 * app.unit; height: width; color: app.ink }
                Text { text: app.volumeReady ? "音量 " + app.volumePercent + "%" : app.volumeHint; color: app.ink; font.pixelSize: 13 * app.unit }
            }
            Text { anchors { right: parent.right; top: parent.top; topMargin: (app.compact ? 8 : 14) * app.unit } text: "实体 V− / V+ 同步"; color: app.secondary; font.pixelSize: 11 * app.unit }
            TouchButton {
                id: volumeDown
                anchors { left: parent.left; bottom: parent.bottom }
                width: Math.max(44, 46 * app.unit); height: Math.max(40, 44 * app.unit)
                icon: "minus"; inset: 12 * app.unit
                Accessible.name: "降低音量"
                enabled: app.volumeReady && !app.volumeAdjustPending && !app.exiting && app.volumePercent > 0
                onClicked: app.adjustVolume(-1)
            }
            Rectangle {
                anchors { left: volumeDown.right; right: volumeUp.left; leftMargin: 14 * app.unit; rightMargin: 14 * app.unit; verticalCenter: volumeDown.verticalCenter }
                height: 5 * app.unit; radius: height / 2; color: app.hairline
                Rectangle { width: parent.width * Math.max(0, Math.min(100, app.volumePercent)) / 100; height: parent.height; radius: parent.radius; color: app.accent }
            }
            TouchButton {
                id: volumeUp
                anchors { right: parent.right; bottom: parent.bottom }
                width: Math.max(44, 46 * app.unit); height: Math.max(40, 44 * app.unit)
                icon: "plus"; inset: 12 * app.unit
                Accessible.name: "提高音量"
                enabled: app.volumeReady && !app.volumeAdjustPending && !app.exiting && app.volumePercent < 100
                onClicked: app.adjustVolume(1)
            }
        }
    }

    Item {
        id: settingsOverlay
        anchors.fill: parent; z: 10; visible: app.settingsOpen
        focus: visible
        Keys.onEscapePressed: app.settingsOpen = false
        Rectangle { anchors.fill: parent; color: "#6618283f" }
        MouseArea { anchors.fill: parent; onClicked: app.settingsOpen = false }
        Rectangle {
            id: settingsSheet
            objectName: "settingsSheet"
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: app.compact ? 10 : 0 }
            height: Math.min(app.height - 20 * app.unit, settingsContents.height + doneButton.height + 58 * app.unit)
            radius: 24 * app.unit; color: app.paper
            MouseArea { anchors.fill: parent }
            Flickable {
                id: settingsScroll
                anchors { left: parent.left; right: parent.right; top: parent.top; bottom: doneButton.top; margins: app.pad; bottomMargin: 15 * app.unit }
                contentWidth: width; contentHeight: settingsContents.height
                boundsBehavior: Flickable.StopAtBounds; clip: true
                Column {
                    id: settingsContents
                    width: settingsScroll.width
                    spacing: 14 * app.unit
                    Item {
                        width: parent.width; height: 64 * app.unit
                        Column {
                            spacing: 4 * app.unit
                            Text { text: "按你的习惯"; color: app.secondary; font.pixelSize: 11 * app.unit }
                            Text { text: "对话设置"; color: app.ink; font.pixelSize: 25 * app.unit; font.weight: Font.Medium }
                        }
                        TouchButton { anchors.right: parent.right; width: 46 * app.unit; height: width; icon: "close"; inset: 12 * app.unit; Accessible.name: "关闭设置"; onClicked: app.settingsOpen = false }
                    }
                    Text { text: "选择模型"; color: app.secondary; font.pixelSize: 13 * app.unit }
                    Repeater {
                        model: ["qwen2-0.5b", "deepseek-r1-1.5b"]
                        TouchButton {
                            objectName: "model-" + modelData
                            width: settingsContents.width; height: 78 * app.unit
                            label: app.modelLabel(modelData)
                            detail: modelData === "qwen2-0.5b" ? "日常问答 · 响应更轻快" : "尝试推理 · 等待时间较长"
                            selected: app.selectedModel === modelData
                            textSize: 17 * app.unit; detailSize: 12 * app.unit
                            enabled: !app.voiceRunning && !app.changingVoice && !app.exiting && app.installedModels.indexOf(modelData) >= 0
                            onClicked: app.selectModel(modelData)
                        }
                    }
                    Text { width: parent.width; text: app.voiceRunning ? "请先停止对话，再更换模型、音色和语速。" : "选择后，点击完成并开始对话。"; color: app.secondary; font.pixelSize: 12 * app.unit; wrapMode: Text.Wrap }
                    Text { text: "音色"; color: app.secondary; font.pixelSize: 13 * app.unit }
                    Row {
                        width: parent.width; spacing: 8 * app.unit
                        Repeater {
                            model: 5
                            TouchButton {
                                width: (settingsContents.width - 32 * app.unit) / 5
                                height: Math.max(44, 48 * app.unit)
                                label: "音色 " + index; selected: app.speaker === index
                                textSize: 13 * app.unit; inset: 2
                                enabled: !app.voiceRunning && !app.changingVoice && !app.exiting
                                onClicked: app.speaker = index
                            }
                        }
                    }
                    Text { text: "语速"; color: app.secondary; font.pixelSize: 13 * app.unit }
                    Flow {
                        width: parent.width; spacing: 8 * app.unit
                        Repeater {
                            model: app.speeds
                            TouchButton {
                                width: (settingsContents.width - 24 * app.unit) / 4
                                height: Math.max(44, 48 * app.unit)
                                label: modelData + "×"; selected: app.speedIndex === index
                                textSize: 15 * app.unit; inset: 4
                                enabled: !app.voiceRunning && !app.changingVoice && !app.exiting
                                onClicked: app.speedIndex = index
                            }
                        }
                    }
                    TouchButton {
                        width: parent.width; height: Math.max(44, 46 * app.unit)
                        label: app.showDiagnostics ? "收起运行日志" : "查看运行日志"
                        flat: true; textSize: 13 * app.unit
                        onClicked: app.showDiagnostics = !app.showDiagnostics
                    }
                    Rectangle {
                        visible: app.showDiagnostics
                        width: parent.width; height: visible ? logText.implicitHeight + 24 * app.unit : 0
                        color: app.background; radius: 12 * app.unit
                        Text {
                            id: logText
                            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 * app.unit }
                            text: "界面 v0.8.0 · 本机日志推导会话状态\n" + (app.snapshotError ? "会话接口：" + app.snapshotError + "\n" : "") + app.notice + "\n\n" + app.lastLog
                            textFormat: Text.PlainText; wrapMode: Text.Wrap
                            color: app.secondary; font.pixelSize: 12 * app.unit; lineHeight: 1.4
                        }
                    }
                }
            }
            TouchButton {
                id: doneButton
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: app.pad }
                height: Math.max(48, 52 * app.unit); primary: true; label: "完成"; textSize: 17 * app.unit
                onClicked: app.settingsOpen = false
            }
        }
    }

    Item {
        id: exitOverlay
        anchors.fill: parent; visible: app.exitConfirm; z: 20
        focus: visible
        Keys.onEscapePressed: app.exitConfirm = false
        Rectangle { anchors.fill: parent; color: "#7718283f" }
        MouseArea { anchors.fill: parent; onClicked: app.exitConfirm = false }
        Rectangle {
            anchors.centerIn: parent
            width: Math.min(parent.width - 2 * app.pad, 420 * app.unit)
            height: exitContents.height + 44 * app.unit
            color: app.paper; radius: 23 * app.unit
            MouseArea { anchors.fill: parent }
            Column {
                id: exitContents
                width: parent.width - 40 * app.unit
                anchors.centerIn: parent
                spacing: 18 * app.unit
                AppIcon { width: 30 * app.unit; height: width; symbol: "exit"; color: app.accent; anchors.horizontalCenter: parent.horizontalCenter }
                Text { width: parent.width; text: "返回桌面？"; color: app.ink; font.pixelSize: 23 * app.unit; font.weight: Font.Medium; horizontalAlignment: Text.AlignHCenter }
                Text { width: parent.width; text: "结束当前对话，返回原厂桌面。"; color: app.secondary; font.pixelSize: 13 * app.unit; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap }
                Row {
                    width: parent.width; spacing: 12 * app.unit
                    TouchButton { width: (parent.width - parent.spacing) / 2; height: Math.max(48, 52 * app.unit); label: "继续使用"; textSize: 15 * app.unit; inset: 6; onClicked: app.exitConfirm = false }
                    TouchButton { width: (parent.width - parent.spacing) / 2; height: Math.max(48, 52 * app.unit); label: "返回桌面"; primary: true; textSize: 15 * app.unit; inset: 6; onClicked: { app.exitConfirm = false; app.requestExit(); } }
                }
            }
        }
    }
}
