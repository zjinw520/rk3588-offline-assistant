#ifndef RK3588_SYSTEMUI_WAKE_BRIDGE_H
#define RK3588_SYSTEMUI_WAKE_BRIDGE_H

#include <QObject>
#include <QString>
#include <functional>

namespace SystemUiWake {
using LogSink = std::function<void(const QString &)>;

// The production startup passes the factory QML window. Selftests pass an
// isolated QQmlEngine QtObject and a temporary socket; no desktop connection.
QObject *createBridge(QObject *root, const QString &socketPath,
                      QString *error = nullptr, const LogSink &log = LogSink());
bool hasCallApp(QObject *root);
}

#endif
