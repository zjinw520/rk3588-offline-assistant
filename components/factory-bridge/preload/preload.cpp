#include "bridge.h"

#include <QCoreApplication>
#include <QDateTime>
#include <QFile>
#include <QFileInfo>
#include <QGuiApplication>
#include <QPointer>
#include <QRegularExpression>
#include <QSet>
#include <QStringList>
#include <QTimer>
#include <QWindow>

#include <dlfcn.h>
#include <sys/stat.h>

namespace {
const QString FactoryExe = QStringLiteral("/opt/ui/systemui");
const QString LogPath = QStringLiteral("/userdata/voice-assistant/systemui-wake-bridge.log");
const QString SocketPath = QStringLiteral("/var/run/rk3588-systemui-wake.sock");
const QString InstalledLibrary = QStringLiteral("/userdata/voice-assistant/libsystemuiwake.so");
QPointer<QObject> activeBridge;

void record(const QString &message)
{
    struct stat info;
    const QByteArray path = QFile::encodeName(LogPath);
    if (::lstat(path.constData(), &info) == 0 && !S_ISREG(info.st_mode))
        return;
    QFile log(LogPath);
    if (log.open(QIODevice::WriteOnly | QIODevice::Append)) {
        const QString line = QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)
                + QStringLiteral(" pid=%1 ").arg(QCoreApplication::applicationPid())
                + message + QChar('\n');
        log.write(line.toUtf8());
        log.flush();
    }
}

void installBridge(int attempt = 0)
{
    if (activeBridge)
        return;
    QGuiApplication *application = qobject_cast<QGuiApplication *>(QCoreApplication::instance());
    QObject *root = nullptr;
    if (application) {
        QSet<QObject *> candidates;
        for (QWindow *window : QGuiApplication::allWindows()) {
            QList<QObject *> objects;
            objects.append(window);
            objects.append(window->findChildren<QObject *>());
            for (QObject *object : objects) {
                if (SystemUiWake::hasCallApp(object))
                    candidates.insert(object);
            }
        }
        if (candidates.size() > 1) {
            record(QStringLiteral("BRIDGE_ERROR multiple-callApp-objects count=%1").arg(candidates.size()));
            return;
        }
        if (!candidates.isEmpty())
            root = *candidates.constBegin();
    }
    if (!root) {
        if (attempt < 50) {
            QTimer::singleShot(100, QCoreApplication::instance(), [attempt]() { installBridge(attempt + 1); });
        } else {
            record(QStringLiteral("BRIDGE_ERROR factory-callApp-window-not-found"));
        }
        return;
    }
    QString error;
    record(QStringLiteral("BRIDGE_ROOT class=") + QString::fromLatin1(root->metaObject()->className()));
    activeBridge = SystemUiWake::createBridge(root, SocketPath, &error, record);
    if (!activeBridge)
        record(QStringLiteral("BRIDGE_START_FAILED ") + error);
}

void rk3588SystemUiWakeStartup()
{
    // Public Qt startup routine only; no intercepted Qt/libc functions and no
    // vendor ABI assumptions. Refuse all other executables and unmarked loads.
    if (qgetenv("RK3588_SYSTEMUI_WAKE_BRIDGE") != QByteArrayLiteral("1"))
        return;
    const QString actualExe = QFileInfo(QStringLiteral("/proc/self/exe")).canonicalFilePath();
    const QString expectedExe = QFileInfo(FactoryExe).canonicalFilePath();
    if (actualExe.isEmpty() || expectedExe.isEmpty() || actualExe != expectedExe)
        return;

    Dl_info libraryInfo;
    const bool located = ::dladdr(reinterpret_cast<const void *>(&rk3588SystemUiWakeStartup), &libraryInfo) != 0
            && libraryInfo.dli_fname;
    const QString libraryPath = located ? QFile::decodeName(libraryInfo.dli_fname) : QString();
    const QString canonicalLibrary = QFileInfo(libraryPath).canonicalFilePath();
    QStringList remaining;
    const QStringList entries = QString::fromLocal8Bit(qgetenv("LD_PRELOAD")).split(
                QRegularExpression(QStringLiteral("[\\s:]+")), Qt::SkipEmptyParts);
    for (const QString &entry : entries) {
        const QString canonicalEntry = QFileInfo(entry).canonicalFilePath();
        if (entry == libraryPath || entry == InstalledLibrary
                || (!canonicalLibrary.isEmpty() && canonicalEntry == canonicalLibrary))
            continue;
        remaining.append(entry);
    }
    if (remaining.isEmpty())
        qunsetenv("LD_PRELOAD");
    else
        qputenv("LD_PRELOAD", remaining.join(QChar(':')).toLocal8Bit());
    qunsetenv("RK3588_SYSTEMUI_WAKE_BRIDGE");
    record(QStringLiteral("BRIDGE_STARTUP exe=") + actualExe + QStringLiteral(" library=") + libraryPath);
    if (!located || canonicalLibrary.isEmpty()) {
        record(QStringLiteral("BRIDGE_ERROR cannot-identify-preload-library"));
        return;
    }
    QTimer::singleShot(0, QCoreApplication::instance(), []() { installBridge(); });
}

Q_COREAPP_STARTUP_FUNCTION(rk3588SystemUiWakeStartup)
}
