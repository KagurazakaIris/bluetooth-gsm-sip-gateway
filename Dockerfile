FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=UTF-8

# Asterisk 18 保留 chan_sip 分机；oFono 负责 HFP 控制，BlueALSA 负责 SCO。
RUN apt-get update && apt-get install -y --no-install-recommends \
        alsa-utils \
        asterisk \
        baresip \
        bluez \
        bluez-alsa-utils \
        ca-certificates \
        curl \
        dbus \
        iproute2 \
        kmod \
        ofono \
        procps \
        python3 \
        python3-dbus \
        python3-gi \
        rfkill \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY config/asterisk /opt/gateway/config/asterisk
RUN cp /opt/gateway/config/asterisk/logger.conf /etc/asterisk/logger.conf && \
    cp /opt/gateway/config/asterisk/rtp.conf /etc/asterisk/rtp.conf && \
    mkdir -p /var/run/dbus /var/lib/bluetooth /var/run/asterisk /var/lib/gateway /var/spool/asterisk/monitor /var/lib/gateway/baresip && \
    chown asterisk:asterisk /var/run/asterisk /var/spool/asterisk/monitor

COPY app /opt/gateway/app
COPY scripts /opt/gateway/scripts
RUN chmod +x /opt/gateway/app/gateway_manager.py /opt/gateway/app/hfp_bridge.py \
        /opt/gateway/scripts/entrypoint.sh /opt/gateway/scripts/gateway-control.sh \
        /opt/gateway/scripts/host-bluetooth-control.sh

ENTRYPOINT ["/opt/gateway/scripts/entrypoint.sh"]
