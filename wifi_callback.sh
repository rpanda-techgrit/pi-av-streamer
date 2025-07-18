#!/bin/bash

WIFI_SSID="Buckingham Slums"
WIFI_PASS="octBorn1510"
WIFI_IFACE="wlan0"
STATIC_IP="192.168.29.101/24"

# 1. Try to connect to known WiFi
echo "Trying to connect to WiFi: $WIFI_SSID"
cat > /etc/wpa_supplicant/wpa_supplicant.conf <<EOF
country=US
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1

network={
    ssid="$WIFI_SSID"
    psk="$WIFI_PASS"
}
EOF

ip link set $WIFI_IFACE down
ip addr flush dev $WIFI_IFACE
ip link set $WIFI_IFACE up

# Use dhcpcd or static IP
dhclient $WIFI_IFACE

# Wait up to 30 seconds for a connection
echo "Waiting for WiFi connection..."
timeout=30
while [ $timeout -gt 0 ]; do
    if ip addr show $WIFI_IFACE | grep "inet $STATIC_IP" >/dev/null 2>&1; then
        echo "Connected with static IP $STATIC_IP"
        exit 0
    fi
    sleep 1
    timeout=$((timeout-1))
done

echo "WiFi not available. Switching to AP mode."

# 2. Switch to AP mode
systemctl stop dhcpcd
systemctl stop wpa_supplicant

ip addr flush dev $WIFI_IFACE
ip addr add $STATIC_IP dev $WIFI_IFACE
ip link set $WIFI_IFACE up

cat > /etc/hostapd/hostapd.conf <<EOF
interface=$WIFI_IFACE
driver=nl80211
ssid=Pi_AP
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=raspberry
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

cat > /etc/dnsmasq.conf <<EOF
interface=$WIFI_IFACE
dhcp-range=192.168.1.100,192.168.1.200,255.255.255.0,24h
address=/#/$STATIC_IP
EOF

systemctl start dnsmasq
systemctl start hostapd

echo "AP started with SSID 'Pi_AP'"
