#include "audio_engine.h"
#include "control_client.h"
#include "main_window.h"
#include "network_discovery.h"
#include "startup_dialog.h"
#include "../shared/winsock_init.h"

#include <QApplication>
#include <QDir>
#include <QIcon>
#include <QFile>
#include <QMessageBox>

#include <cstdlib>
#include <iostream>

namespace {
constexpr const char* DEFAULT_ROOM = "main";

bool register_client(const std::string& server_ip, const std::string& client_id, int audio_port, const std::string& secret) {
    const std::string command = "REGISTER:" + client_id + ":" + std::to_string(audio_port) + ":" + secret;
    const auto resp = send_control_command(server_ip, command);
    if (!resp.ok) {
        std::cerr << "[CLIENT] Registration error: " << resp.response << "\n";
        return false;
    }
    if (resp.response == "TAKEN") {
        std::cerr << "[CLIENT] Client ID " << client_id << " already taken\n";
        return false;
    }
    if (resp.response != "OK") {
        std::cerr << "[CLIENT] Unexpected registration response: " << resp.response << "\n";
        return false;
    }

    const auto join_resp = send_control_command(server_ip, "JOIN:" + client_id + ":" + DEFAULT_ROOM);
    if (!join_resp.ok || join_resp.response.rfind("OK", 0) != 0) {
        std::cerr << "[CLIENT] JOIN failed for client " << client_id << ": " << join_resp.response << "\n";
        return false;
    }
    return true;
}

QString app_icon_path() {
    QDir dir(QCoreApplication::applicationDirPath());
    const QString icon_path = dir.filePath("technical-support.ico");
    return icon_path;
}

} // namespace

int main(int argc, char* argv[]) {
    WinSockInit wsa;
    if (!wsa.ok()) {
        std::cerr << "[CLIENT] Winsock initialization failed\n";
        return 1;
    }

    QApplication app(argc, argv);
    QDir::setCurrent(QCoreApplication::applicationDirPath());

    const QString icon_path = app_icon_path();
    if (QFile::exists(icon_path)) {
        app.setWindowIcon(QIcon(icon_path));
    }

    NetworkDiscovery discovery;
    std::cout << "[CLIENT] Discovering server...\n";
    discovery.discover();

    QString server_ip;
    if (discovery.server_ip().empty()) {
        std::cout << "[CLIENT] Server not found, prompting for manual IP...\n";
        ServerIPDialog dlg;
        if (dlg.exec() == QDialog::Accepted) {
            server_ip = dlg.serverIp();
            std::cout << "[CLIENT] Using manual server IP: " << server_ip.toStdString() << "\n";
        } else {
            std::cout << "[CLIENT] User cancelled, exiting\n";
            return 0;
        }
    } else {
        server_ip = QString::fromStdString(discovery.server_ip());
        std::cout << "[CLIENT] Server found at: " << server_ip.toStdString() << "\n";
    }

    StartupDialog startup(server_ip, 0);
    if (startup.exec() != QDialog::Accepted) {
        std::cout << "[CLIENT] User cancelled client setup, exiting\n";
        return 0;
    }

    const QString client_id = startup.clientId();
    std::cout << "[CLIENT] Selected Client ID: " << client_id.toStdString() << "\n";

    AudioEngine audio;
    const int audio_port = audio.port();
    std::cout << "[CLIENT] Audio engine initialized on port " << audio_port << "\n";

    const char* secret_env = std::getenv("VOICE_REGISTER_SECRET");
    const std::string secret = secret_env ? secret_env : "mysecret";

    std::cout << "[CLIENT] Registering with server...\n";
    if (!register_client(server_ip.toStdString(), client_id.toStdString(), audio_port, secret)) {
        QMessageBox msg;
        msg.setIcon(QMessageBox::Critical);
        msg.setWindowTitle("Registration Failed");
        msg.setText(QString("Client ID %1 is already in use or registration failed!").arg(client_id));
        msg.setInformativeText("Please choose a different client ID and try again.");
        msg.exec();
        audio.shutdown();
        return 1;
    }

    std::cout << "[CLIENT] Registration successful - starting UI...\n";

    MainWindow window(client_id, server_ip, &audio, QString::fromStdString(secret));
    window.show();
    return app.exec();
}
