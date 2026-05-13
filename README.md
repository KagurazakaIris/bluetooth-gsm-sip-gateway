# Bluetooth GSM ↔ SIP Gateway

Turn your Linux PC into a softphone for your mobile’s GSM calls. This project runs Asterisk with the chan_mobile Bluetooth gateway inside a Docker container, so you can:

- Receive incoming GSM calls on your PC (ring a SIP client like Linphone)
- Place outgoing calls from your SIP client via your mobile phone over Bluetooth

It uses BlueZ + D-Bus for Bluetooth, and Asterisk to bridge GSM↔SIP audio.


## How It Works

- A privileged container starts `dbus`, `bluetoothd` (BlueZ), and `asterisk`.
- The container reuses the host Bluetooth pairing database from `/var/lib/bluetooth`.
- Asterisk loads `chan_mobile` and connects to your paired phone over Bluetooth RFCOMM/HFP.
- Dialplan bridges calls:
  - Incoming GSM → rings SIP extension `1001`.
  - Outgoing from SIP → dials out using the paired mobile device.

Key files in this repo configure that flow:
- `docker-compose.yml` — host integration and device access
- `Dockerfile` — Ubuntu 18.04 + BlueZ + Asterisk runtime
- `chan_mobile.conf` / `mobile.conf` — Bluetooth adapter and phone mapping
- `sip.conf` — local SIP account `1001` for your softphone
- `extensions.conf` — dialplan bridging SIP↔Mobile
- `rtp.conf`, `logger.conf` — media and logging
- `start.sh` — launches dbus, bluetoothd, and asterisk in-foreground
- `host-config/tel_url_handler/linphone-tel`, `host-config/tel_url_handler/linphone-tel.desktop` — optional tel: link handler for Linphone


## Requirements

- Linux host with working Bluetooth adapter
- Docker and Docker Compose
- A SIP softphone on the host (e.g., Linphone)
- A mobile phone with Bluetooth Hands‑Free support (HFP)

Notes
- Container runs with `privileged`, `network_mode: host`, and `/dev` bind. This is necessary for BlueZ/Bluetooth access and Asterisk audio bridging.
- Ubuntu 18.04 base is used to match Asterisk and BlueZ versions known to work with `chan_mobile`.
- Not compatible with Docker Desktop installed via App Center/Snap/Flatpak. Those setups typically cannot expose host D-Bus and the physical Bluetooth adapter to containers. Use a native Docker Engine installation on the host OS.

Why Docker?
- Asterisk’s `chan_mobile` has long‑standing compatibility issues with the modern Bluetooth stack in newer Ubuntu releases (e.g., 24.04). On up‑to‑date hosts, Asterisk often fails to cooperate reliably with BlueZ/DBus for HFP/RFCOMM, causing unstable pairing, device discovery, or Audio Gateway operation. This container pins an older, known‑good userspace (Ubuntu 18.04 with matching BlueZ/Asterisk) so you don’t need to downgrade your entire OS just to make GSM↔SIP bridging work.


## Docker on Ubuntu 24.x (native install)

Avoid Docker Desktop from App Center/Snap/Flatpak — it won’t pass through host D-Bus or the Bluetooth adapter in the way this stack requires. Install Docker Engine natively from Docker’s official APT repository.

1) Optional: remove conflicting packages (Docker Desktop, legacy docker.io)
- These commands are safe if those packages are absent.
  ```bash
  sudo apt remove -y docker-desktop || true
  sudo apt remove -y docker.io docker-doc podman-docker containerd runc || true
  # Do NOT remove your data unless you intend to:
  # sudo rm -rf /var/lib/docker /var/lib/containerd
  ```

2) Install prerequisites and add Docker’s APT repo
  ```bash
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg

  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release; echo $VERSION_CODENAME) stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update
  ```

3) Install Docker Engine + Compose plugin
  ```bash
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo systemctl enable --now docker
  ```

4) (Optional) Run Docker as your user
  ```bash
  sudo usermod -aG docker $USER
  # Log out and back in (or reboot) so your group membership applies
  ```

5) Verify
  ```bash
  docker run --rm hello-world
  docker compose version
  ```


## Quick Start

0) Pair and trust your phone on the host
- Do this on the host, not inside the container. Use your desktop Bluetooth UI or `bluetoothctl`:
  ```bash
  bluetoothctl
  power on
  agent on
  default-agent
  scan on   # wait until you see your phone (e.g., XX:XX:XX:XX:XX:XX)
  pair XX:XX:XX:XX:XX:XX
  trust XX:XX:XX:XX:XX:XX
  connect XX:XX:XX:XX:XX:XX
  quit
  ```

1) Adjust Bluetooth addresses
- The repo tracks `chan_mobile.conf.example` and `mobile.conf.example`.
- Copy them to local `chan_mobile.conf` and `mobile.conf` before editing MAC addresses. The local `.conf` files are ignored by git.
- Edit `chan_mobile.conf` (and `mobile.conf` for compatibility) and set:
  - `[adapter] address=` to your host’s Bluetooth adapter MAC.
  - `[mobile_phone] address=` to your phone’s Bluetooth MAC.
  - Keep `port=3` unless you know your HFP/RFCOMM channel differs. To discover your phone’s HFP/RFCOMM channel, run:
    - `sudo sdptool records XX:XX:XX:XX:XX:XX | grep -i -A10 "Audio Gateway\|Handsfree"`
    Replace the MAC and use the reported RFCOMM channel for `port`.

2) Hand Bluetooth control to the container (first-time or as needed)
- If you see conflicts with the host's `bluetoothd`, let the container own Bluetooth (dbus + bluetoothd) and claim the adapter.
  ```bash
  sudo systemctl stop bluetooth || true
  sudo systemctl disable bluetooth || true
  sudo rfkill unblock bluetooth
  sudo modprobe btusb bluetooth rfcomm
  sudo hciconfig hci0 up
  hciconfig -a   # look for UP RUNNING
  ```

  To use Bluetooth on the host again later, stop the container and re-enable the host service:
  ```bash
  docker compose down
  sudo systemctl enable bluetooth
  sudo systemctl start bluetooth
  sudo rfkill unblock bluetooth
  sudo hciconfig hci0 up
  ```

3) Build and run
- `docker compose build`
- `docker compose up -d`

4) Verify the phone auto-connects
- Watch logs: `docker logs -f asterisk-mobile`
- Optional one-shot checks:
  - `docker exec asterisk-mobile asterisk -rx "mobile show devices"`
  - `docker exec asterisk-mobile asterisk -rx "mobile show connections"`
- If the phone was already paired and trusted on the host, no manual pairing inside the container should be needed.

5) Register your SIP client
- Configure your SIP softphone to register to:
  - Server: `127.0.0.1`
  - User: `1001`
  - Password: `1234` (change it!)
  - Transport: UDP

6) Test
- Outgoing: dial a number from your SIP client; it should go out via your phone.
- Incoming: call your mobile; your SIP client should ring as extension `1001`.

## Linphone Config (Host)

Install and point Linphone (Desktop or CLI) to Asterisk on the host, and use the provided configs.

- Install Linphone
  - Ubuntu 24.x: from App Center (or via apt/flatpak). Any recent Linphone Desktop or `linphonec` works.

- Copy the config
  - For Linphone Desktop (GUI):
    - `mkdir -p "$HOME/.config/linphone"`
    - `cp host-config/linphone/linphonerc "$HOME/.config/linphone/linphonerc"`
  - For Linphone CLI (`linphonec`):
    - `mkdir -p "$HOME/.config/linphone"`
    - `cp host-config/linphone/linphonerc-asterisk "$HOME/.config/linphone/linphonerc"`

- Adjust credentials if needed
  - In `~/.config/linphone/linphonerc`, under `[proxy_0]` set:
    - `reg_proxy=sip:127.0.0.1;transport=udp` (or your Asterisk IP)
    - `reg_identity=sip:1001@127.0.0.1` (or your extension@server)
  - Linphone will prompt for the password on first register unless you add an `[auth_info_*]` section.

- About these config files
  - They are full configs (not minimal). Linphone may auto-extend/modify them on first run — that’s expected.
  - Personal/Sensitive values was redacted from the versions in this repo:
    - `[sip] contact=...` replaced with a generic `sip:1001@127.0.0.1` to avoid exposing usernames/hosts.
    - `[misc] uuid=...` replaced with `<uuid>`.
    - `[sound] *_dev_id=...` changed to `PulseAudio Default` to avoid exposing specific hardware names.
    - `[auth_info_0] ha1=...` replaced with `<ha1>` because it is a password hash.
  - If you prefer persistent credentials delete the whole `[auth_info_*]` section and enter credentials via the UI once; Linphone will recreate it.

- Notes
  - You don’t need to remove Linphone’s auto-generated sections; keeping them is fine.

## Configuration Details

Asterisk SIP account: `sip.conf`
- `[1001]` is a local user for your softphone.
- Change `secret`, and adapt codecs (`alaw`/`ulaw`) to your needs.

Bluetooth gateway: `chan_mobile.conf` (and `mobile.conf`)
- `[adapter] id=usb` and `address=<adapter-mac>` point to your adapter.
- `[mobile_phone] address=<phone-mac>`, `port=3`, `context=from-mobile`, `adapter=usb`.
- On some phones the HFP/RFCOMM channel can differ; if calls fail to connect, scan channels with `sdptool browse <phone-mac>` and update `port` accordingly.

Dialplan: `extensions.conf`
- Global `MOBILE_IFACE=mobile_phone` maps to your phone section.
- `from-sip`: anything dialed `_X.` from SIP is sent to `Mobile/${MOBILE_IFACE}/${EXTEN}`.
- `from-mobile`: incoming GSM calls dial `SIP/1001` to ring your softphone.

RTP/media: `rtp.conf`
- RTP range narrowed to `10000–10100`. Open these UDP ports if you firewall.

Logging: `logger.conf`
- `console` and `full` log files enabled for diagnostics.

Container: `docker-compose.yml`
- `privileged: true`, `network_mode: host`, `/dev`, and `/var/lib/bluetooth` passthrough enable Bluetooth access and reuse the host pairing database.

Entrypoint: `start.sh`
- Starts a private system `dbus`, `bluetoothd --compat`, then `asterisk -f -vvv`.


## Operating Asterisk

Optional: attach to the running Asterisk CLI for debugging:
- `docker exec -it asterisk-mobile asterisk -rvvvvv`

Useful commands:
- `core show channels`
- `core set verbose 5`
- `mobile show devices`
- `mobile show connections`
- `mobile status`
- `sip show peers` (for chan_sip)

Logs:
- `docker logs -f asterisk-mobile`
- Container log files are also available under `/var/log/asterisk/` if you need deeper debugging.


## Linphone tel: Link Handler (Optional)

If you use Linphone on the host, you can make `tel:` links dial via Linphone automatically.

1) Create a small helper:
- Save `host-config/tel_url_handler/linphone-tel` to `~/.local/bin/linphone-tel` and make it executable:
  - `mkdir -p ~/.local/bin`
  - `cp host-config/tel_url_handler/linphone-tel ~/.local/bin/linphone-tel`
  - `chmod +x ~/.local/bin/linphone-tel`

2) Register the desktop handler:
- Copy `host-config/tel_url_handler/linphone-tel.desktop` to `~/.local/share/applications/`:
  - `mkdir -p ~/.local/share/applications`
  - `cp host-config/tel_url_handler/linphone-tel.desktop ~/.local/share/applications/`
- Update and set default handler:
  - `update-desktop-database ~/.local/share/applications/`
  - `xdg-mime default linphone-tel.desktop x-scheme-handler/tel`
  - Verify: `xdg-mime query default x-scheme-handler/tel` → `linphone-tel.desktop`

The helper normalizes numbers (strips spaces, keeps digits/`+`, converts `+` to `00`) and launches Linphone with `call sip-address=...`.


## Troubleshooting

Switching Bluetooth control (host ↔ container)
- Give control to the container (if host `bluetoothd` conflicts):
  ```bash
  sudo systemctl stop bluetooth || true
  sudo systemctl disable bluetooth || true
  sudo rfkill unblock bluetooth
  sudo hciconfig hci0 up
  ```
- Return control to the host:
  ```bash
  docker compose down
  sudo systemctl enable bluetooth
  sudo systemctl start bluetooth
  sudo rfkill unblock bluetooth
  sudo hciconfig hci0 up
  # Optionally: bluetoothctl → power on
  ```

Bluetooth doesn’t start in container
- Check container privileges match `docker-compose.yml`.
- Inspect `docker logs -f asterisk-mobile` after start.
- Run one-shot diagnostics with `docker exec asterisk-mobile rfkill list`, `docker exec asterisk-mobile hciconfig -a`, and `docker exec asterisk-mobile dmesg | tail -n 100`.

Phone not pairing or connecting
- Pair and trust the phone on the host first while the host `bluetoothd` service is running.
- Ensure `/var/lib/bluetooth` is bind-mounted into the container and the MAC addresses in `chan_mobile.conf` match the host bond.
- After host pairing, stop the host `bluetoothd` service before starting the container so the container can claim the adapter.

Outgoing calls fail immediately
- Verify `port` in `chan_mobile.conf` matches phone’s HFP/RFCOMM channel (`sdptool browse <mac>`).
- Confirm `mobile show devices` shows your device as `Connected`.

No audio / one‑way audio
- Open RTP UDP range in firewall: `10000–10100`.
- Check `directmedia=no` and codec compatibility in `sip.conf`.

SIP client won’t register
- Confirm SIP points to `127.0.0.1`, user `1001`, password changed and correct.
- `sip show peers` should list `1001` as `OK` with latency.

Asterisk module not loading `chan_mobile`
- Verify the image build completed successfully and `chan_mobile.so` exists in `/usr/lib/asterisk/modules/`.
- Run `docker exec asterisk-mobile asterisk -rx "module show like chan_mobile.so"` and review Asterisk logs for load errors.


## Security Notes

- Change default SIP credentials in `sip.conf` before use.
- Limit exposed services; this stack uses `network_mode: host`. Prefer local network only.
- Keep OS and Docker updated. Ubuntu 18.04 is EOL; for long‑term use, consider updating the base and re‑validating `chan_mobile`.


## FAQ

- Do I need both `chan_mobile.conf` and `mobile.conf`?
  - Some Asterisk builds use `chan_mobile.conf`. This repo includes both (same content) for compatibility; adjust both or prefer `chan_mobile.conf`.
- Do I need to pair inside the container?
  - No. Pair and trust the phone on the host first; the container reuses `/var/lib/bluetooth` and auto-attempts the connection on startup.
- Which SIP client should I use?
  - Linphone, baresip, or any basic SIP client. Start simple: PC and container on the same host with UDP transport.


---

Contributions and improvements welcome.
