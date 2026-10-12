import re


USER_RE = re.compile(r"[A-Za-z0-9_.-]{1,31}\Z")


def asterisk_sip(user, secret):
    if not USER_RE.fullmatch(user) or not secret or len(secret) > 128 or any(char in secret for char in "\r\n"):
        raise ValueError("SIP 分机或密码无效")
    return (
        "[general]\n"
        "udpbindaddr=0.0.0.0:5060\n"
        "transport=udp\n"
        "allowguest=no\n"
        "srvlookup=yes\n"
        "context=reject-unknown\n"
        "disallow=all\n"
        "allow=alaw\n"
        "allow=ulaw\n\n"
        f"[{user}]\n"
        "type=friend\n"
        "host=dynamic\n"
        f"secret={secret}\n"
        "context=from-sip\n"
        f"callerid=\"Softphone\" <{user}>\n"
        "disallow=all\nallow=alaw\nallow=ulaw\n"
        "nat=force_rport,comedia\n"
        "qualify=yes\ndirectmedia=no\ndtmfmode=rfc2833\n\n"
        "[hfp]\n"
        "type=peer\n"
        "host=127.0.0.1\n"
        "port=5070\n"
        "insecure=port,invite\n"
        "context=from-hfp\n"
        "disallow=all\nallow=alaw\nallow=ulaw\n"
        "directmedia=no\n"
    )


def asterisk_extensions(user):
    if not USER_RE.fullmatch(user):
        raise ValueError("SIP 分机无效")
    return (
        "[general]\nstatic=yes\nwriteprotect=no\nclearglobalvars=no\n\n"
        "[reject-unknown]\n"
        "exten => _X.,1,Hangup()\n\n"
        "[from-sip]\n"
        # TODO: 如需支持从 Linphone 拨打 +E.164 号码，增加安全的拨号计划匹配规则。
        "exten => _X.,1,NoOp(Outgoing HFP to ${EXTEN})\n"
        " same => n,Set(CALLERID(num)=${EXTEN})\n"
        " same => n,Dial(SIP/hfp@hfp,60,U(record-call^outbound))\n"
        " same => n,Hangup()\n\n"
        "[from-hfp]\n"
        f"exten => {user},1,NoOp(Incoming HFP to SIP/{user})\n"
        f" same => n,Dial(SIP/{user},30,U(record-call^inbound))\n"
        " same => n,Hangup()\n\n"
        "[record-call]\n"
        "exten => s,1,MixMonitor(/var/spool/asterisk/monitor/${ARG1}-${STRFTIME(${EPOCH},,%Y%m%d-%H%M%S)}-${UNIQUEID}.wav,b)\n"
        " same => n,Return()\n"
    )


def baresip_config():
    return (
        "module_path /usr/lib/baresip/modules\n"
        "sip_listen 127.0.0.1:5070\n"
        "ctrl_tcp_listen 127.0.0.1:4444\n"
        "audio_player alsa,plughw:Loopback,0,0\n"
        "audio_source alsa,plughw:Loopback,0,1\n"
        "audio_alert alsa,plughw:Loopback,0,0\n"
        "audio_srate 8000\n"
        "audio_channels 1\n"
        "menu_bell no\n"
        "ringback_disabled yes\n"
        "module g711.so\n"
        "module alsa.so\n"
        "module account.so\n"
        "module menu.so\n"
        "module ctrl_tcp.so\n"
    )


def baresip_accounts():
    return "<sip:hfp@127.0.0.1>;regint=0\n"
