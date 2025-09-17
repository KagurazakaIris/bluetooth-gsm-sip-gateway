FROM ubuntu:18.04

ENV DEBIAN_FRONTEND=noninteractive

# Tooling + BlueZ + dbus + Asterisk + utilities
RUN apt-get update && \
    apt-get install -y \
        build-essential \
        wget curl git vim nano \
        pkg-config \
        libbluetooth-dev \
        libusb-1.0-0-dev \
        libdb-dev \
        libdbus-1-dev \
        bluez bluez-tools \
        dbus \
        asterisk \
        rfkill iproute2 kmod \
    && rm -rf /var/lib/apt/lists/*

# Create required runtime dirs (dbus, bluetooth, asterisk)
RUN mkdir -p /var/run/dbus /var/lib/bluetooth /var/run/asterisk && \
    chown asterisk:asterisk /var/run/asterisk

# Configs (can be overridden via bind-mounts)
COPY mobile.conf /etc/asterisk/mobile.conf
COPY chan_mobile.conf /etc/asterisk/chan_mobile.conf
COPY extensions.conf /etc/asterisk/extensions.conf
COPY logger.conf /etc/asterisk/logger.conf
COPY rtp.conf /etc/asterisk/rtp.conf
COPY sip.conf /etc/asterisk/sip.conf

# Entry script
COPY start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]
