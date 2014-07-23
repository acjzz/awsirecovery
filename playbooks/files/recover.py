#!/usr/bin/python
import os

username = "recovery_user"
os.chroot("/chroot")
os.system("useradd %s"%username)
os.system("echo '%s ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers"%username)
os.system("mkdir /home/%s/.ssh"%username)
os.system("mv /key /home/%s/.ssh/authorized_keys"%username)