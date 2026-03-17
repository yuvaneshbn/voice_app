#ifndef UI_HELPERS_H
#define UI_HELPERS_H

#include <QString>
#include <QWidget>

#include <stdexcept>
#include <string>

template <typename T>
T* require_child(QObject* parent, const char* object_name) {
    auto* widget = parent->findChild<T*>(object_name);
    if (!widget) {
        throw std::runtime_error(std::string("Missing required widget '") + object_name + "'");
    }
    return widget;
}

template <typename T>
T* find_first_child(QObject* parent, const char* name1, const char* name2) {
    if (auto* widget = parent->findChild<T*>(name1)) {
        return widget;
    }
    return parent->findChild<T*>(name2);
}

QWidget* load_ui_widget(const QString& ui_path, QWidget* parent = nullptr);
QString ui_path(const QString& filename);

#endif
