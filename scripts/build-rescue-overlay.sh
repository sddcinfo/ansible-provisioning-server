#!/bin/bash
# build-rescue-overlay.sh - Build rescue.apkovl.tar.gz for Alpine Linux PXE rescue
# Creates a self-contained overlay that configures Alpine for SSH-based rescue operations
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="${1:-/var/www/html/provisioning/alpine-rescue}"
SSH_PUBKEY_FILE="${2:-$HOME/.ssh/sysadmin_automation_key.pub}"
BUILD_DIR=$(mktemp -d)

trap 'rm -rf "$BUILD_DIR"' EXIT

echo "Building rescue overlay..."
echo "  Output: ${OUTPUT_DIR}/rescue.apkovl.tar.gz"
echo "  SSH key: ${SSH_PUBKEY_FILE}"

# Validate SSH key exists
if [ ! -f "$SSH_PUBKEY_FILE" ]; then
    echo "ERROR: SSH public key not found: ${SSH_PUBKEY_FILE}"
    exit 1
fi

# Generate root password hash (password: rescue)
ROOT_HASH=$(openssl passwd -6 'rescue' 2>/dev/null || python3 -c "import crypt; print(crypt.crypt('rescue', crypt.mksalt(crypt.METHOD_SHA512)))")

# Create directory structure
mkdir -p "$BUILD_DIR"/etc/network
mkdir -p "$BUILD_DIR"/etc/apk
mkdir -p "$BUILD_DIR"/etc/ssh
mkdir -p "$BUILD_DIR"/etc/local.d
mkdir -p "$BUILD_DIR"/etc/runlevels/default
mkdir -p "$BUILD_DIR"/etc/runlevels/boot
mkdir -p "$BUILD_DIR"/root/.ssh

# etc/hostname
echo "rescue" > "$BUILD_DIR"/etc/hostname

# NOTE: We do NOT include /etc/passwd, /etc/shadow, or /etc/group in the overlay.
# Alpine's default system files from alpine-baselayout include all required system
# users (sshd, nobody, etc). Overwriting them breaks privilege separation for sshd.
# The root password is set at boot time by rescue-init.start via sed on /etc/shadow.

# etc/network/interfaces - loopback only; DHCP handled dynamically in rescue-init.start
cat > "$BUILD_DIR"/etc/network/interfaces << 'EOF'
auto lo
iface lo inet loopback
EOF

# etc/apk/world - packages to install
cat > "$BUILD_DIR"/etc/apk/world << 'EOF'
openssh
curl
nvme-cli
parted
gptfdisk
util-linux
e2fsprogs
dosfstools
lvm2
smartmontools
pciutils
ipmitool
bash
gcompat
libc6-compat
efibootmgr
tpm2-tools
tpm2-tss
tpm2-tss-tcti-device
efitools
cryptsetup
mdadm
EOF

# etc/apk/repositories
cat > "$BUILD_DIR"/etc/apk/repositories << 'EOF'
http://10.10.1.1/alpine/v3.21/main
http://10.10.1.1/alpine/v3.21/community
https://dl-cdn.alpinelinux.org/alpine/v3.21/main
https://dl-cdn.alpinelinux.org/alpine/v3.21/community
EOF

# etc/ssh/sshd_config - Alpine openssh has no PAM support, do not use UsePAM
cat > "$BUILD_DIR"/etc/ssh/sshd_config << 'EOF'
Port 22
PermitRootLogin yes
PasswordAuthentication yes
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
X11Forwarding no
PrintMotd yes
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/ssh/sftp-server
EOF

# etc/local.d/rescue-init.start - Boot initialization script
cat > "$BUILD_DIR"/etc/local.d/rescue-init.start << SCRIPT
#!/bin/sh
# rescue-init.start - Initialize rescue environment on boot

# Set root password (password: rescue)
# We modify the existing /etc/shadow rather than replacing it, to preserve
# all system user entries (sshd, nobody, etc) needed by openssh
sed -i 's|^root:.*|root:${ROOT_HASH}:19757:0:::::|' /etc/shadow

# Read kernel parameters
RESCUE_HOSTNAME=""
RESCUE_SERVER=""
for param in \$(cat /proc/cmdline); do
    case "\$param" in
        rescue_hostname=*) RESCUE_HOSTNAME="\${param#rescue_hostname=}" ;;
        rescue_server=*)   RESCUE_SERVER="\${param#rescue_server=}" ;;
    esac
done

# Set hostname from kernel param if provided
if [ -n "\$RESCUE_HOSTNAME" ]; then
    hostname "\$RESCUE_HOSTNAME"
    echo "\$RESCUE_HOSTNAME" > /etc/hostname
fi

# Bring up ALL ethernet interfaces via DHCP (not just eth0)
# The provisioning NIC may not be eth0
for iface in /sys/class/net/eth* /sys/class/net/en*; do
    [ -d "\$iface" ] || continue
    ifname=\$(basename "\$iface")
    echo "Bringing up \$ifname via DHCP..."
    ip link set "\$ifname" up 2>/dev/null || true
    udhcpc -i "\$ifname" -n -q 2>/dev/null || true
done

# Fetch and mount modloop for kernel modules (NVMe, IPMI, etc)
# The initramfs modloop fetch can fail silently in netboot; do it explicitly
MODLOOP_URL=""
for param in \$(cat /proc/cmdline); do
    case "\$param" in
        modloop=*) MODLOOP_URL="\${param#modloop=}" ;;
    esac
done
if [ -n "\$MODLOOP_URL" ] && [ ! -d /lib/modules ]; then
    echo "Fetching modloop from \$MODLOOP_URL..."
    mkdir -p /tmp/modloop-mount
    if curl -sfL -o /tmp/modloop.squashfs "\$MODLOOP_URL" && \
       mount -t squashfs /tmp/modloop.squashfs /tmp/modloop-mount && \
       ln -sf /tmp/modloop-mount/modules /lib/modules; then
        echo "Modloop mounted, loading hardware drivers..."
        modprobe nvme 2>/dev/null || true
        modprobe ipmi_devintf 2>/dev/null || true
        modprobe ipmi_si 2>/dev/null || true
        modprobe tpm_tis 2>/dev/null || true
        modprobe efivarfs 2>/dev/null || true
    else
        echo "WARNING: Failed to fetch/mount modloop (hardware tools may not work)"
    fi
fi

# Install packages from apk world file
echo "Installing rescue packages..."
apk update 2>/dev/null || true
apk add --no-cache \
    openssh curl nvme-cli parted gptfdisk util-linux \
    e2fsprogs dosfstools lvm2 smartmontools pciutils \
    ipmitool bash gcompat libc6-compat \
    efibootmgr tpm2-tools tpm2-tss tpm2-tss-tcti-device efitools \
    cryptsetup mdadm \
    2>/dev/null || echo "WARNING: Some packages failed to install"

# Generate SSH host keys if missing
ssh-keygen -A 2>/dev/null

# Re-apply SSH config after openssh package install (package may overwrite it)
cat > /etc/ssh/sshd_config << 'SSHCONF'
Port 22
PermitRootLogin yes
PasswordAuthentication yes
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
X11Forwarding no
PrintMotd yes
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/ssh/sftp-server
SSHCONF

# Ensure authorized_keys is in place with correct permissions and ownership
mkdir -p /root/.ssh
chmod 700 /root/.ssh
chown root:root /root/.ssh/authorized_keys 2>/dev/null || true
chmod 600 /root/.ssh/authorized_keys 2>/dev/null || true

# Ensure sshd privilege separation requirements are met
mkdir -p /var/empty
chmod 755 /var/empty

# Mount efivarfs for efibootmgr and secure boot key operations
if [ -d /sys/firmware/efi/efivars ]; then
    mount -t efivarfs efivarfs /sys/firmware/efi/efivars 2>/dev/null || true
fi

# Download SUM (Supermicro Update Manager) for in-band BIOS operations
if [ -n "\$RESCUE_SERVER" ]; then
    echo "Downloading SUM tool..."
    mkdir -p /opt/sum
    curl -sfL -o /opt/sum/sum "http://\${RESCUE_SERVER}/provisioning/alpine-rescue/tools/sum" && \
        chmod +x /opt/sum/sum && \
        ln -sf /opt/sum/sum /usr/local/bin/sum && \
        echo "SUM installed to /opt/sum/sum" || \
        echo "WARNING: SUM download failed (non-fatal)"
fi

# Start SSH daemon
rc-service sshd restart 2>/dev/null || rc-service sshd start 2>/dev/null || /usr/sbin/sshd

# Find MAC address from any active interface for callback
MAC=""
for iface in /sys/class/net/eth* /sys/class/net/en*; do
    [ -d "\$iface" ] || continue
    ifname=\$(basename "\$iface")
    if ip -4 addr show "\$ifname" 2>/dev/null | grep -q 'inet '; then
        MAC=\$(cat "/sys/class/net/\$ifname/address" 2>/dev/null)
        [ -n "\$MAC" ] && break
    fi
done
[ -z "\$MAC" ] && MAC="unknown"

# Callback to provisioning server
if [ -n "\$RESCUE_SERVER" ]; then
    echo "Sending RESCUE callback to \${RESCUE_SERVER} (MAC: \${MAC})..."
    curl -sf "http://\${RESCUE_SERVER}/?action=callback&mac=\${MAC}&status=RESCUE" || \
        echo "WARNING: Callback to provisioning server failed"
fi

echo "Rescue environment ready. SSH access available as root."
echo "  Password: rescue"
echo "  IPs: \$(ip -4 -o addr show | grep -v '127.0.0.1' | awk '{print \$4}' | tr '\n' ' ')"
SCRIPT
chmod +x "$BUILD_DIR"/etc/local.d/rescue-init.start

# Runlevel symlinks
# NOTE: Do NOT symlink sshd here - openssh isn't installed yet at boot time
# and the dangling symlink causes OpenRC errors. The rescue-init.start script
# starts sshd explicitly after installing the package.
ln -sf /etc/init.d/local "$BUILD_DIR"/etc/runlevels/default/local
ln -sf /etc/init.d/networking "$BUILD_DIR"/etc/runlevels/boot/networking

# root/.ssh/authorized_keys
cp "$SSH_PUBKEY_FILE" "$BUILD_DIR"/root/.ssh/authorized_keys
chmod 700 "$BUILD_DIR"/root/.ssh
chmod 600 "$BUILD_DIR"/root/.ssh/authorized_keys

# Build the tarball
mkdir -p "$OUTPUT_DIR"
(cd "$BUILD_DIR" && tar czf "$OUTPUT_DIR"/rescue.apkovl.tar.gz --owner=0 --group=0 \
    etc/hostname \
    etc/network/interfaces \
    etc/apk/world \
    etc/apk/repositories \
    etc/ssh/sshd_config \
    etc/local.d/rescue-init.start \
    etc/runlevels/default/local \
    etc/runlevels/boot/networking \
    root/.ssh/authorized_keys)

echo "Overlay built: ${OUTPUT_DIR}/rescue.apkovl.tar.gz"
echo "Contents:"
tar tzf "$OUTPUT_DIR"/rescue.apkovl.tar.gz
