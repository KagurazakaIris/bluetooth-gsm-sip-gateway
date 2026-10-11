FROM ubuntu:18.04

ENV DEBIAN_FRONTEND=noninteractive
ARG ASTERISK_VERSION=13.18.3

# 工具、BlueZ、D-Bus、Asterisk 及编译和运行依赖
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
        procps \
        python3 \
        rfkill \
        util-linux \
        uuid-dev \
        vim \
        wget \
        bluez bluez-tools \
        dbus \
        asterisk \
    && rm -rf /var/lib/apt/lists/*

# 从官方源码重建 Asterisk，并显式启用 chan_mobile。
# 先清理发行版模块，避免残留 ABI 不匹配的动态库。
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

# 创建 D-Bus、蓝牙和 Asterisk 运行目录
RUN mkdir -p /var/run/dbus /var/lib/bluetooth /var/run/asterisk && \
    chown asterisk:asterisk /var/run/asterisk

# 基础配置，蓝牙地址由管理服务在运行时生成
COPY gateway-manager/chan_mobile.unconfigured.conf /etc/asterisk/mobile.conf
COPY gateway-manager/chan_mobile.unconfigured.conf /etc/asterisk/chan_mobile.conf
COPY extensions.conf /etc/asterisk/extensions.conf
COPY logger.conf /etc/asterisk/logger.conf
COPY rtp.conf /etc/asterisk/rtp.conf
COPY sip.conf /etc/asterisk/sip.conf

# 蓝牙管理服务和宿主机控制权交接脚本
COPY gateway-manager /opt/gateway-manager
RUN chmod +x \
        /opt/gateway-manager/gateway_manager.py \
        /opt/gateway-manager/gateway-control.sh \
        /opt/gateway-manager/host-bluetooth-control.sh && \
    mkdir -p /var/lib/gateway

# 容器入口脚本
COPY start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]
