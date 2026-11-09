#include "systemui_bridge.h"
#include "protocol_selftest.h"

#include <QByteArray>
#include <QCoreApplication>
#include <QDebug>
#include <QDir>
#include <QFileInfo>
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQuickWindow>
#include <QTimer>
#include <QUrl>

static int inspectSystemUi(int argc, char *argv[])
{
    // QCoreApplication deliberately avoids creating a Wayland window. This
    // mode acquires metadata but never announces Active, answers probes, or
    // sends a command to Home.
    QCoreApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("rk3588-ai-inspect"));
    SystemUiBridge bridge(nullptr, true);
    QObject::connect(&bridge, &SystemUiBridge::inspectionFinished, &app, [&app]() {
        qInfo() << "SYSTEMUI_INSPECT_DONE v0.4.3";
        app.quit();
    });
    QTimer::singleShot(6000, &app, [&app]() {
        qCritical() << "SYSTEMUI_INSPECT_TIMEOUT v0.4.3";
        app.exit(2);
    });
    return app.exec();
}

int main(int argc, char *argv[])
{
    if (argc > 1 && QByteArray(argv[1]) == QByteArrayLiteral("--inspect-systemui"))
        return inspectSystemUi(argc, argv);
    if (argc > 1 && QByteArray(argv[1]) == QByteArrayLiteral("--self-test-systemui"))
        return runSystemUiSelfTest(argc, argv);
    QGuiApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("rk3588-ai"));
    QCoreApplication::setApplicationVersion(QStringLiteral("0.4.3"));
    QString path = QDir(QCoreApplication::applicationDirPath()).filePath(QStringLiteral("Main.qml"));
    if (app.arguments().size() > 1)
        path = app.arguments().at(1);
    if (!QFileInfo::exists(path)) {
        qCritical() << "QML_FILE_MISSING" << path;
        return 1;
    }

    QQmlApplicationEngine engine;
    QObject::connect(&engine, &QQmlApplicationEngine::quit, &app, &QCoreApplication::quit);
    engine.load(QUrl::fromLocalFile(QFileInfo(path).absoluteFilePath()));
    if (engine.rootObjects().isEmpty()) {
        qCritical() << "QML_LOAD_FAILED";
        return 1;
    }
    auto *window = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
    if (!window) {
        qCritical() << "QML_ROOT_IS_NOT_WINDOW";
        return 1;
    }

    SystemUiBridge bridge;
    QObject::connect(&bridge, &SystemUiBridge::showRequested, window, [window]() {
        window->showFullScreen();
        window->raise();
        window->requestActivate();
    });
    QObject::connect(&bridge, &SystemUiBridge::quitRequested, window, [window]() {
        // Reuse QML's existing voice-stop/exit path, including its timeout.
        QMetaObject::invokeMethod(window, "requestExit", Qt::QueuedConnection);
    });
    QObject::connect(&app, &QCoreApplication::aboutToQuit, &bridge, &SystemUiBridge::returnToHome);
    qInfo() << "NATIVE_HOST_READY v0.4.3";
    return app.exec();
}
