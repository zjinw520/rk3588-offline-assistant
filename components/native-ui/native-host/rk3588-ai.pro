QT += core gui qml quick remoteobjects
CONFIG += c++11 console
CONFIG -= app_bundle
TEMPLATE = app

# The factory launcher uses pidof with the manifest's application name.
TARGET = rk3588-ai
SOURCES += main.cpp systemui_bridge.cpp protocol_selftest.cpp
HEADERS += systemui_bridge.h protocol_selftest.h
