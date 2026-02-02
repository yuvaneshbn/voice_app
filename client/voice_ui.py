# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'voice.ui'
##
## Created by: Qt User Interface Compiler version 6.10.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QPushButton,
    QSizePolicy, QStatusBar, QWidget)

class Ui_project1(object):
    def setupUi(self, project1):
        if not project1.objectName():
            project1.setObjectName(u"project1")
        project1.resize(749, 571)
        self.centralwidget = QWidget(project1)
        self.centralwidget.setObjectName(u"centralwidget")
        self.cl1talkbtn = QPushButton(self.centralwidget)
        self.cl1talkbtn.setObjectName(u"cl1talkbtn")
        self.cl1talkbtn.setGeometry(QRect(190, 170, 81, 81))
        self.cl2talkbtn = QPushButton(self.centralwidget)
        self.cl2talkbtn.setObjectName(u"cl2talkbtn")
        self.cl2talkbtn.setGeometry(QRect(280, 170, 81, 81))
        self.cl3talkbtn = QPushButton(self.centralwidget)
        self.cl3talkbtn.setObjectName(u"cl3talkbtn")
        self.cl3talkbtn.setGeometry(QRect(370, 170, 81, 81))
        self.client4talkbtn = QPushButton(self.centralwidget)
        self.client4talkbtn.setObjectName(u"client4talkbtn")
        self.client4talkbtn.setGeometry(QRect(460, 170, 81, 81))
        self.talkbtn = QPushButton(self.centralwidget)
        self.talkbtn.setObjectName(u"talkbtn")
        self.talkbtn.setGeometry(QRect(310, 290, 121, 61))
        self.cl3hearbtn = QPushButton(self.centralwidget)
        self.cl3hearbtn.setObjectName(u"cl3hearbtn")
        self.cl3hearbtn.setGeometry(QRect(370, 70, 81, 81))
        self.cl2hearbtn = QPushButton(self.centralwidget)
        self.cl2hearbtn.setObjectName(u"cl2hearbtn")
        self.cl2hearbtn.setGeometry(QRect(280, 70, 81, 81))
        self.cl4hearbtn = QPushButton(self.centralwidget)
        self.cl4hearbtn.setObjectName(u"cl4hearbtn")
        self.cl4hearbtn.setGeometry(QRect(460, 70, 81, 81))
        self.cl1hearbtn = QPushButton(self.centralwidget)
        self.cl1hearbtn.setObjectName(u"cl1hearbtn")
        self.cl1hearbtn.setGeometry(QRect(190, 70, 81, 81))
        self.HEAR = QLabel(self.centralwidget)
        self.HEAR.setObjectName(u"HEAR")
        self.HEAR.setGeometry(QRect(110, 100, 55, 16))
        self.BROADCAST = QLabel(self.centralwidget)
        self.BROADCAST.setObjectName(u"BROADCAST")
        self.BROADCAST.setGeometry(QRect(200, 310, 71, 16))
        self.TALK = QLabel(self.centralwidget)
        self.TALK.setObjectName(u"TALK")
        self.TALK.setGeometry(QRect(110, 200, 55, 16))
        project1.setCentralWidget(self.centralwidget)
        self.statusbar = QStatusBar(project1)
        self.statusbar.setObjectName(u"statusbar")
        project1.setStatusBar(self.statusbar)

        self.retranslateUi(project1)

        QMetaObject.connectSlotsByName(project1)
    # setupUi

    def retranslateUi(self, project1):
        project1.setWindowTitle(QCoreApplication.translate("project1", u"VOICE", None))
        self.cl1talkbtn.setText(QCoreApplication.translate("project1", u"CLIENT 1", None))
        self.cl2talkbtn.setText(QCoreApplication.translate("project1", u"CLIENT 2", None))
        self.cl3talkbtn.setText(QCoreApplication.translate("project1", u"CLIENT 3", None))
        self.client4talkbtn.setText(QCoreApplication.translate("project1", u"CLIENT 4", None))
        self.talkbtn.setText(QCoreApplication.translate("project1", u"TALK", None))
        self.cl3hearbtn.setText(QCoreApplication.translate("project1", u"CLIENT 3", None))
        self.cl2hearbtn.setText(QCoreApplication.translate("project1", u"CLIENT 2", None))
        self.cl4hearbtn.setText(QCoreApplication.translate("project1", u"CLIENT 4", None))
        self.cl1hearbtn.setText(QCoreApplication.translate("project1", u"CLIENT 1", None))
        self.HEAR.setText(QCoreApplication.translate("project1", u"HEAR", None))
        self.BROADCAST.setText(QCoreApplication.translate("project1", u"BROADCAST", None))
        self.TALK.setText(QCoreApplication.translate("project1", u"TALK", None))
    # retranslateUi

