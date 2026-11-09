QT += core gui network
CONFIG += shared c++11 warn_on
TEMPLATE = lib
TARGET = systemuiwake
DESTDIR = $$PWD/dist
SOURCES += bridge.cpp preload.cpp
HEADERS += bridge.h
LIBS += -ldl
