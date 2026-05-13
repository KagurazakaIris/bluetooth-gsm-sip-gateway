FROM ubuntu:18.04

ENV DEBIAN_FRONTEND=noninteractive
ARG ASTERISK_VERSION=13.18.3

# Tooling + BlueZ + dbus + Asterisk + build/runtime dependencies
RUN apt-get update && \
    apt-get install -y \
        autoconf \
        automake \
        bison \
        build-essential \
        ca-certificates \
        curl \
        flex \
        git \
        iproute2 \
        kmod \
        libbluetooth-dev \
        libcurl4-openssl-dev \
        libdb-dev \
        libdbus-1-dev \
        libedit-dev \
        libjansson-dev \
        libncurses5-dev \
        libogg-dev \
        libspeex-dev \
        libspeexdsp-dev \
        libsrtp2-dev \
        libsqlite3-dev \
        libssl-dev \
        libtool \
        libusb-1.0-0-dev \
        libvorbis-dev \
        libxml2-dev \
        nano \
        pkg-config \
        rfkill \
        uuid-dev \
        vim \
        wget \
        bluez bluez-tools \
        dbus \
        asterisk \
    && rm -rf /var/lib/apt/lists/*

# Rebuild Asterisk from official source and enable chan_mobile explicitly.
# Clean out distro-shipped modules first so we do not keep mismatched .so files.
RUN rm -rf /usr/lib/asterisk/modules/* && \
    curl -L "https://downloads.asterisk.org/pub/telephony/asterisk/old-releases/asterisk-${ASTERISK_VERSION}.tar.gz" -o /tmp/asterisk.tar.gz && \
    mkdir -p /tmp/asterisk-src && \
    tar -xf /tmp/asterisk.tar.gz -C /tmp/asterisk-src --strip-components=1 && \
    cd /tmp/asterisk-src && \
    ./configure --with-bluetooth && \
    make menuselect.makeopts && \
    menuselect/menuselect --enable chan_mobile menuselect.makeopts && \
    make -j"$(nproc)" && \
    make install && \
    test -f /usr/lib/asterisk/modules/chan_mobile.so && \
    rm -rf /tmp/asterisk-src /tmp/asterisk.tar.gz

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
