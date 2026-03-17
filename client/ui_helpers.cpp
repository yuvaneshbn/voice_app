#include "ui_helpers.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QUiLoader>

QWidget* load_ui_widget(const QString& ui_path, QWidget* parent) {
    QUiLoader loader;
    QFile ui_file(ui_path);
    if (!ui_file.open(QIODevice::ReadOnly)) {
        throw std::runtime_error("Unable to open UI file: " + ui_path.toStdString());
    }
    QWidget* widget = loader.load(&ui_file, parent);
    ui_file.close();
    if (!widget) {
        throw std::runtime_error("Unable to load UI file: " + ui_path.toStdString());
    }
    return widget;
}

QString ui_path(const QString& filename) {
    QDir dir(QCoreApplication::applicationDirPath());
    return dir.filePath(QStringLiteral("ui/%1").arg(filename));
}
