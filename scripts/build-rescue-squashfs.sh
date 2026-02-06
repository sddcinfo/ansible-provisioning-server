#!/bin/bash
# build-rescue-squashfs.sh - Build Ubuntu 24.04 rescue squashfs for PXE boot
# Creates a minimal Ubuntu rootfs with rescue tools, packages as squashfs
# Requires: debootstrap, squashfs-tools, root privileges
# Usage: build-rescue-squashfs.sh [-v] [output_dir] [ssh_pubkey_file]
set -euo pipefail

# Parse flags
VERBOSE=0
while getopts "v" opt; do
    case "$opt" in
        v) VERBOSE=1 ;;
        *) echo "Usage: $0 [-v] [output_dir] [ssh_pubkey_file]"; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="${1:-/var/www/html/provisioning/ubuntu-rescue}"
_REAL_HOME="${SUDO_USER:+$(eval echo ~$SUDO_USER)}"
_REAL_HOME="${_REAL_HOME:-$HOME}"
SSH_PUBKEY_FILE="${2:-${_REAL_HOME}/.ssh/sysadmin_automation_key.pub}"
BUILD_DIR=$(mktemp -d)
ROOTFS="${BUILD_DIR}/rootfs"
UBUNTU_CODENAME="noble"
UBUNTU_MIRROR="http://archive.ubuntu.com/ubuntu"

# Verbosity control: fd 3 is verbose output (stdout or /dev/null)
if [ "$VERBOSE" -eq 1 ]; then
    exec 3>&1
else
    exec 3>/dev/null
fi

# Track what is mounted so cleanup is reliable
MOUNTED_POINTS=()

mount_chroot() {
    local target="$1"
    # Use fresh proc/sysfs instances (not bind) -- they unmount cleanly
    # and don't leak host kernel state into the squashfs
    mount -t proc proc "${target}/proc"
    MOUNTED_POINTS+=("${target}/proc")

    mount -t sysfs sysfs "${target}/sys"
    MOUNTED_POINTS+=("${target}/sys")

    # Bind-mount /dev (needed for device nodes during package install)
    mount --bind /dev "${target}/dev"
    MOUNTED_POINTS+=("${target}/dev")

    mount --bind /dev/pts "${target}/dev/pts"
    MOUNTED_POINTS+=("${target}/dev/pts")
}

umount_chroot() {
    # Unmount in reverse order
    local i
    for (( i=${#MOUNTED_POINTS[@]}-1 ; i>=0 ; i-- )); do
        local mp="${MOUNTED_POINTS[$i]}"
        if mountpoint -q "$mp" 2>/dev/null; then
            umount "$mp" 2>/dev/null || umount -l "$mp" 2>/dev/null || true
        fi
    done
    MOUNTED_POINTS=()
}

cleanup() {
    echo "Cleaning up..."
    umount_chroot
    # Verify nothing is still mounted under BUILD_DIR before rm
    if findmnt -R "$BUILD_DIR" >/dev/null 2>&1; then
        echo "WARNING: Filesystems still mounted under ${BUILD_DIR}, attempting lazy unmount..."
        findmnt -R "$BUILD_DIR" -o TARGET -n 2>/dev/null | sort -r | while read -r mp; do
            umount -l "$mp" 2>/dev/null || true
        done
    fi
    rm -rf "$BUILD_DIR" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Building Ubuntu 24.04 Rescue Squashfs ==="
echo "  Output: ${OUTPUT_DIR}"
echo "  SSH key: ${SSH_PUBKEY_FILE}"
echo "  Build dir: ${BUILD_DIR}"
echo ""

# Validate prerequisites
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (need chroot access)"
    exit 1
fi

if [ ! -f "$SSH_PUBKEY_FILE" ]; then
    echo "ERROR: SSH public key not found: ${SSH_PUBKEY_FILE}"
    exit 1
fi

for cmd in debootstrap mksquashfs; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "ERROR: Required command not found: ${cmd}"
        echo "Install with: apt install debootstrap squashfs-tools"
        exit 1
    fi
done

# Generate root password hash (password: rescue)
ROOT_HASH=$(openssl passwd -6 'rescue')

# Step 1: Debootstrap minimal Ubuntu rootfs
echo "=== Step 1/6: Debootstrap ==="
debootstrap --variant=minbase --include=systemd,systemd-sysv \
    "$UBUNTU_CODENAME" "$ROOTFS" "$UBUNTU_MIRROR" >&3 2>&3
echo "  Done."

# Step 2: Configure APT sources and install packages
echo "=== Step 2/6: Installing packages ==="

cat > "${ROOTFS}/etc/apt/sources.list" << EOF
deb ${UBUNTU_MIRROR} ${UBUNTU_CODENAME} main restricted universe
deb ${UBUNTU_MIRROR} ${UBUNTU_CODENAME}-updates main restricted universe
deb ${UBUNTU_MIRROR} ${UBUNTU_CODENAME}-security main restricted universe
EOF

# Copy host resolv.conf so DNS works inside chroot
cp /etc/resolv.conf "${ROOTFS}/etc/resolv.conf"

mount_chroot "$ROOTFS"

# Prevent services from starting during package install
cat > "${ROOTFS}/usr/sbin/policy-rc.d" << 'EOF'
#!/bin/sh
exit 101
EOF
chmod +x "${ROOTFS}/usr/sbin/policy-rc.d"

# Run apt inside chroot -- use a script file so we get proper error handling
# (bash -c with set -e inside a string doesn't reliably propagate errors)
cat > "${ROOTFS}/tmp/install-packages.sh" << 'INSTALLEOF'
#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

apt-get update -qq
apt-get install -y --no-install-recommends \
    linux-image-generic \
    casper \
    openssh-server \
    curl \
    bash \
    coreutils \
    net-tools \
    iproute2 \
    iputils-ping \
    ca-certificates \
    nvme-cli \
    parted \
    gdisk \
    util-linux \
    e2fsprogs \
    dosfstools \
    lvm2 \
    mdadm \
    cryptsetup-bin \
    smartmontools \
    pciutils \
    ipmitool \
    efibootmgr \
    tpm2-tools \
    efitools \
    systemd-resolved \
    sudo \
    wget \
    less \
    vim-tiny \
    linux-headers-generic \
    build-essential \
    memtester \
    stress-ng \
    lm-sensors \
    fio \
    edac-utils \
    lshw \
    sysstat \
    dmidecode

apt-get clean
rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /tmp/install-packages.sh
INSTALLEOF
chmod +x "${ROOTFS}/tmp/install-packages.sh"

# Execute install script -- errors will now properly propagate
chroot "$ROOTFS" /tmp/install-packages.sh >&3 2>&3

rm -f "${ROOTFS}/usr/sbin/policy-rc.d"

echo "  Done."

# Step 2b: Build SUM BIOS kernel module
echo "=== Step 2b/6: Building sum_bios.ko ==="

SUM_DRIVER_SRC="$SCRIPT_DIR/../../supermicro-sum-scripts/sum_2.14.0_Linux_x86_64/driver/Source/Linux"
if [ -d "$SUM_DRIVER_SRC" ]; then
    mkdir -p "${ROOTFS}/tmp/sum_driver"
    cp "$SUM_DRIVER_SRC"/sum_bios.c "$SUM_DRIVER_SRC"/sum_bios.h "$SUM_DRIVER_SRC"/Makefile \
        "${ROOTFS}/tmp/sum_driver/"

    # Patch sum_bios.c for kernel 6.4+ API changes:
    #   - class_create() no longer takes THIS_MODULE argument (6.4+)
    #   - devnode callback requires const struct device* (6.4+)
    #   - inb/outb/outl/virt_to_phys need <linux/io.h> (6.x)
    sed -i 's/class_create(THIS_MODULE, "sum_bios")/class_create("sum_bios")/' \
        "${ROOTFS}/tmp/sum_driver/sum_bios.c"
    sed -i 's/static char\* sum_bios_devnode(struct device\* dev,/static char* sum_bios_devnode(const struct device* dev,/' \
        "${ROOTFS}/tmp/sum_driver/sum_bios.c"
    # Add missing include for IO port and virt_to_phys functions
    sed -i '/#include <linux\/blkdev.h>/a #include <linux\/io.h>' \
        "${ROOTFS}/tmp/sum_driver/sum_bios.c"
    # Suppress -Werror for vendor code warnings (unused vars, int-to-ptr)
    sed -i 's/EXTRA_CFLAGS += $(DEBFLAGS)/EXTRA_CFLAGS += $(DEBFLAGS) -Wno-error/' \
        "${ROOTFS}/tmp/sum_driver/Makefile"

    # Determine installed kernel version inside the chroot
    CHROOT_KVER=$(ls "${ROOTFS}/lib/modules/" | sort -V | tail -1)
    echo "  Building for kernel: ${CHROOT_KVER}"

    chroot "$ROOTFS" bash -c "
        cd /tmp/sum_driver
        make KERNELDIR=/lib/modules/${CHROOT_KVER}/build 2>&1
    " >&3 2>&3

    if [ -f "${ROOTFS}/tmp/sum_driver/sum_bios.ko" ]; then
        mkdir -p "${ROOTFS}/lib/modules/${CHROOT_KVER}/extra"
        cp "${ROOTFS}/tmp/sum_driver/sum_bios.ko" "${ROOTFS}/lib/modules/${CHROOT_KVER}/extra/"
        chroot "$ROOTFS" depmod "${CHROOT_KVER}" 2>&3
        echo "  sum_bios.ko installed"
    else
        echo "  WARNING: sum_bios.ko build failed (non-fatal)"
    fi

    rm -rf "${ROOTFS}/tmp/sum_driver"
else
    echo "  WARNING: SUM driver source not found at ${SUM_DRIVER_SRC}"
fi

# Remove build deps to save space (kernel headers are ~200MB)
chroot "$ROOTFS" bash -c "
    export DEBIAN_FRONTEND=noninteractive
    apt-get remove -y --purge linux-headers-generic linux-headers-* build-essential cpp gcc make 2>/dev/null || true
    apt-get autoremove -y --purge 2>/dev/null || true
    apt-get clean
    rm -rf /var/lib/apt/lists/*
" >&3 2>&3
echo "  Build deps removed."

# Step 3: Configure the rootfs
echo "=== Step 3/6: Configuring rootfs ==="

# Set root password
chroot "$ROOTFS" bash -c "echo 'root:${ROOT_HASH}' | chpasswd -e" 2>&3

# SSH configuration
mkdir -p "${ROOTFS}/root/.ssh"
cp "$SSH_PUBKEY_FILE" "${ROOTFS}/root/.ssh/authorized_keys"
chmod 700 "${ROOTFS}/root/.ssh"
chmod 600 "${ROOTFS}/root/.ssh/authorized_keys"

# sshd_config.d should exist now that openssh-server is installed
cat > "${ROOTFS}/etc/ssh/sshd_config.d/rescue.conf" << 'EOF'
PermitRootLogin yes
PubkeyAuthentication yes
PasswordAuthentication yes
EOF

chroot "$ROOTFS" systemctl enable ssh 2>&3

# systemd-networkd: DHCP on all ethernet interfaces
mkdir -p "${ROOTFS}/etc/systemd/network"
cat > "${ROOTFS}/etc/systemd/network/20-dhcp.network" << 'EOF'
[Match]
Name=en* eth*

[Network]
DHCP=yes

[DHCP]
UseDNS=yes
UseHostname=false
EOF

chroot "$ROOTFS" systemctl enable systemd-networkd 2>&3

# Set default target to multi-user (casper/live sets graphical.target which never activates)
chroot "$ROOTFS" systemctl set-default multi-user.target 2>&3

# Serial console: autologin on ttyS0
mkdir -p "${ROOTFS}/etc/systemd/system/serial-getty@ttyS0.service.d"
cat > "${ROOTFS}/etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf" << 'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root -o '-p -- \\u' --keep-baud 115200,57600,38400,9600 %I $TERM
EOF

chroot "$ROOTFS" systemctl enable serial-getty@ttyS0 2>&3

# rescue-init service
cat > "${ROOTFS}/usr/local/bin/rescue-init.sh" << 'RESCUESCRIPT'
#!/bin/bash
# rescue-init.sh - Initialize Ubuntu rescue environment on boot

# Read kernel parameters
RESCUE_HOSTNAME=""
RESCUE_SERVER=""
RESCUE_ACTION=""
for param in $(cat /proc/cmdline); do
    case "$param" in
        rescue_hostname=*) RESCUE_HOSTNAME="${param#rescue_hostname=}" ;;
        rescue_server=*)   RESCUE_SERVER="${param#rescue_server=}" ;;
        rescue_action=*)   RESCUE_ACTION="${param#rescue_action=}" ;;
    esac
done

# Set hostname
if [ -n "$RESCUE_HOSTNAME" ]; then
    hostnamectl set-hostname "$RESCUE_HOSTNAME" 2>/dev/null || \
        hostname "$RESCUE_HOSTNAME"
fi

# Mount efivarfs if not already mounted
if [ -d /sys/firmware/efi/efivars ] && ! mountpoint -q /sys/firmware/efi/efivars; then
    mount -t efivarfs efivarfs /sys/firmware/efi/efivars 2>/dev/null || true
fi

# Load SUM BIOS kernel module (required for in-band BIOS config)
if modprobe sum_bios 2>/dev/null; then
    echo "sum_bios module loaded"
elif insmod /lib/modules/$(uname -r)/extra/sum_bios.ko 2>/dev/null; then
    echo "sum_bios module loaded (insmod)"
else
    echo "WARNING: sum_bios module not available"
fi

# Download SUM from provisioning server
if [ -n "$RESCUE_SERVER" ]; then
    echo "Downloading SUM tool..."
    mkdir -p /opt/sum
    if curl -sfL -o /opt/sum/sum "http://${RESCUE_SERVER}/provisioning/ubuntu-rescue/tools/sum"; then
        chmod +x /opt/sum/sum
        ln -sf /opt/sum/sum /usr/local/bin/sum
        echo "SUM installed to /opt/sum/sum"
    else
        echo "WARNING: SUM download failed (non-fatal)"
    fi
fi

# Find MAC address from any active interface for callback
MAC=""
for iface in /sys/class/net/eth* /sys/class/net/en*; do
    [ -d "$iface" ] || continue
    ifname=$(basename "$iface")
    if ip -4 addr show "$ifname" 2>/dev/null | grep -q 'inet '; then
        MAC=$(cat "/sys/class/net/$ifname/address" 2>/dev/null)
        [ -n "$MAC" ] && break
    fi
done
[ -z "$MAC" ] && MAC="unknown"

# Send RESCUE callback to provisioning server
if [ -n "$RESCUE_SERVER" ]; then
    echo "Sending RESCUE callback to ${RESCUE_SERVER} (MAC: ${MAC})..."
    curl -sf "http://${RESCUE_SERVER}/?action=callback&mac=${MAC}&status=RESCUE" || \
        echo "WARNING: Callback to provisioning server failed"
fi

# If rescue_action is set, execute it
if [ "$RESCUE_ACTION" = "install-os" ] && [ -n "$RESCUE_SERVER" ]; then
    echo "Auto-executing OS installation..."
    HOSTNAME_ARG="${RESCUE_HOSTNAME:-unknown}"
    curl -sfL "http://${RESCUE_SERVER}/rescue-scripts/install-incusos.sh" | \
        bash -s -- "$RESCUE_SERVER" "$HOSTNAME_ARG"
    # install-incusos.sh handles reboot
    exit 0
fi

echo ""
echo "============================================"
echo "  Ubuntu Rescue Environment Ready"
echo "  Password: rescue"
echo "  IPs: $(ip -4 -o addr show | grep -v '127.0.0.1' | awk '{print $4}' | tr '\n' ' ')"
echo "============================================"
RESCUESCRIPT
chmod +x "${ROOTFS}/usr/local/bin/rescue-init.sh"

# Create systemd service for rescue-init
cat > "${ROOTFS}/etc/systemd/system/rescue-init.service" << 'EOF'
[Unit]
Description=Rescue Environment Initialization
After=network.target ssh.service
Wants=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/rescue-init.sh
RemainAfterExit=yes
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
EOF

chroot "$ROOTFS" systemctl enable rescue-init 2>&3

# Set default hostname
echo "rescue" > "${ROOTFS}/etc/hostname"

# Clean up resolv.conf -- point to systemd-resolved stub for booted systems
rm -f "${ROOTFS}/etc/resolv.conf"
ln -s /run/systemd/resolve/stub-resolv.conf "${ROOTFS}/etc/resolv.conf"

# Remove machine-id so it gets regenerated per-boot (avoids DHCP conflicts)
rm -f "${ROOTFS}/etc/machine-id" "${ROOTFS}/var/lib/dbus/machine-id"

echo "  Done."

# Step 4: Unmount chroot filesystems
echo "=== Step 4/6: Unmounting chroot ==="
umount_chroot

# Verify nothing is still mounted -- CRITICAL before squashfs build
if findmnt -R "$ROOTFS" -n >/dev/null 2>&1; then
    echo "ERROR: Filesystems still mounted under ${ROOTFS}!"
    findmnt -R "$ROOTFS"
    exit 1
fi
echo "  All clean."

# Step 5: Extract kernel and initrd
echo "=== Step 5/6: Extracting kernel and initrd ==="
mkdir -p "$OUTPUT_DIR"

KERNEL_VERSION=$(ls "${ROOTFS}/boot/vmlinuz-"* 2>/dev/null | sort -V | tail -1 | sed 's|.*/vmlinuz-||')
if [ -z "$KERNEL_VERSION" ]; then
    echo "ERROR: No kernel found in rootfs"
    exit 1
fi
echo "  Kernel: ${KERNEL_VERSION}"

cp "${ROOTFS}/boot/vmlinuz-${KERNEL_VERSION}" "${OUTPUT_DIR}/vmlinuz"
cp "${ROOTFS}/boot/initrd.img-${KERNEL_VERSION}" "${OUTPUT_DIR}/initrd"

echo "  vmlinuz: $(du -h "${OUTPUT_DIR}/vmlinuz" | cut -f1)"
echo "  initrd:  $(du -h "${OUTPUT_DIR}/initrd" | cut -f1)"

# Step 6: Build squashfs
echo "=== Step 6/6: Building squashfs ==="

# Remove kernel/initrd from rootfs to save space (already extracted)
rm -f "${ROOTFS}/boot/vmlinuz-"* "${ROOTFS}/boot/initrd.img-"*

# Use -wildcards with pattern/* to EXCLUDE contents but KEEP empty directories
# as mount points. This is critical -- /proc, /sys, /dev, /run must exist as
# empty dirs in the squashfs for the live system to mount them at boot.
mksquashfs "$ROOTFS" "${OUTPUT_DIR}/filesystem.squashfs" \
    -noappend \
    -no-duplicates \
    -no-recovery \
    -comp xz -b 1M \
    -wildcards \
    -e 'proc/*' 'sys/*' 'dev/*' 'run/*' 'tmp/*' \
    -e 'var/cache/apt/archives/*.deb' >&3 2>&1

echo "  filesystem.squashfs: $(du -h "${OUTPUT_DIR}/filesystem.squashfs" | cut -f1)"

echo ""
echo "=== Build complete ==="
echo "Output files in ${OUTPUT_DIR}:"
ls -lh "${OUTPUT_DIR}/"
