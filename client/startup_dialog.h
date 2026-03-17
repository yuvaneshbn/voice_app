#ifndef STARTUP_DIALOG_H
#define STARTUP_DIALOG_H

#include <QDialog>

class QLineEdit;

class ServerIPDialog : public QDialog {
    Q_OBJECT

public:
    explicit ServerIPDialog(QWidget* parent = nullptr);
    QString serverIp() const { return server_ip_; }

protected:
    void accept() override;

private:
    QLineEdit* ip_input_ = nullptr;
    QString server_ip_;
};

class StartupDialog : public QDialog {
    Q_OBJECT

public:
    explicit StartupDialog(const QString& server_ip, int audio_port, QWidget* parent = nullptr);
    QString clientId() const { return client_id_; }

protected:
    void accept() override;

private:
    bool isNameAvailable(const QString& candidate);
    static QStringList parseListResponse(const QString& response);

    QString server_ip_;
    int audio_port_ = 0;
    QLineEdit* name_input_ = nullptr;
    QString client_id_;
};

#endif
