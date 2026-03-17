#include "startup_dialog.h"

#include "control_client.h"

#include <QHBoxLayout>
#include <QIcon>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPushButton>
#include <QVBoxLayout>

// No additional networking needed here; control checks handled via send_control_command.

ServerIPDialog::ServerIPDialog(QWidget* parent)
    : QDialog(parent) {
    setWindowTitle("Voice App - Server Not Found");
    setFixedSize(400, 150);

    auto* layout = new QVBoxLayout(this);
    layout->addWidget(new QLabel("Server not found via broadcast."));
    layout->addWidget(new QLabel("Enter server IP address:"));

    ip_input_ = new QLineEdit(this);
    ip_input_->setPlaceholderText("192.168.0.X");
    layout->addWidget(ip_input_);

    auto* btn_layout = new QHBoxLayout();
    auto* ok_btn = new QPushButton("Connect", this);
    auto* cancel_btn = new QPushButton("Cancel", this);
    connect(ok_btn, &QPushButton::clicked, this, &ServerIPDialog::accept);
    connect(cancel_btn, &QPushButton::clicked, this, &ServerIPDialog::reject);
    btn_layout->addWidget(ok_btn);
    btn_layout->addWidget(cancel_btn);
    layout->addLayout(btn_layout);
}

void ServerIPDialog::accept() {
    const QString ip = ip_input_->text().trimmed();
    if (!ip.isEmpty()) {
        server_ip_ = ip;
        QDialog::accept();
    }
}

StartupDialog::StartupDialog(const QString& server_ip, int audio_port, QWidget* parent)
    : QDialog(parent), server_ip_(server_ip), audio_port_(audio_port) {
    setWindowTitle("Voice App - Client Setup");
    setFixedSize(400, 200);

    auto* layout = new QVBoxLayout(this);
    layout->addWidget(new QLabel(QString("Server: %1").arg(server_ip_)));
    layout->addWidget(new QLabel(QString("Audio Port: %1").arg(audio_port_)));
    layout->addWidget(new QLabel("Enter your unique name:"));

    name_input_ = new QLineEdit(this);
    name_input_->setPlaceholderText("e.g. Alice / ControlRoom-1");
    connect(name_input_, &QLineEdit::returnPressed, this, &StartupDialog::accept);
    layout->addWidget(name_input_);

    auto* btn_layout = new QHBoxLayout();
    auto* ok_btn = new QPushButton("OK", this);
    auto* cancel_btn = new QPushButton("Cancel", this);
    connect(ok_btn, &QPushButton::clicked, this, &StartupDialog::accept);
    connect(cancel_btn, &QPushButton::clicked, this, &StartupDialog::reject);
    btn_layout->addWidget(ok_btn);
    btn_layout->addWidget(cancel_btn);
    layout->addLayout(btn_layout);
}

void StartupDialog::accept() {
    QString candidate = name_input_->text().trimmed();
    candidate = candidate.endsWith(',') ? candidate.left(candidate.size() - 1) : candidate;

    if (candidate.isEmpty()) {
        QMessageBox::warning(this, "Invalid Name", "Name cannot be empty.");
        return;
    }

    if (candidate.contains(':') || candidate.contains('|') || candidate.contains(',') || candidate.contains('\n') || candidate.contains('\r') || candidate.contains('\t')) {
        QMessageBox::warning(this, "Invalid Name", "Name cannot contain ':', '|', ',' or control characters.");
        return;
    }

    if (!isNameAvailable(candidate)) {
        QMessageBox::warning(this, "Name Already In Use", QString("'%1' is already connected.\nPlease choose a different name.").arg(candidate));
        return;
    }

    client_id_ = candidate;
    QDialog::accept();
}

bool StartupDialog::isNameAvailable(const QString& candidate) {
    const auto resp = send_control_command(server_ip_.toStdString(), "LIST", 2500);
    if (!resp.ok) {
        return true;
    }
    const QStringList existing = parseListResponse(QString::fromStdString(resp.response));
    return !existing.contains(candidate);
}

QStringList StartupDialog::parseListResponse(const QString& response) {
    if (response.isEmpty()) {
        return {};
    }
    if (response.contains('\n')) {
        return response.split('\n', Qt::SkipEmptyParts);
    }
    return response.split(',', Qt::SkipEmptyParts);
}
