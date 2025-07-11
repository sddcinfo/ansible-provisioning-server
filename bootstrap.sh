#!/bin/bash
# This script prepares the control node to run the provisioning server playbook.
set -e
echo "--- Installing Ansible and dependencies ---"
sudo apt-get update
sudo apt-get install -y ansible git

echo "--- Configuring Netplan ---"
sudo rm -f /etc/netplan/*.yaml
sudo bash -c 'cat << EOF > /etc/netplan/01-netcfg.yaml
# This file is managed by Ansible.
network:
  version: 2
  ethernets:
    ens33:
      addresses:
        - 10.10.1.50/24
      # gateway4: 10.10.1.1 # Uncomment if this server is the gateway
      nameservers:
        addresses: [10.10.1.1]
    ens34:
      dhcp4: true
EOF'
sudo chmod 600 /etc/netplan/01-netcfg.yaml
sudo netplan apply

echo "--- Installing required Ansible collections ---"
ansible-galaxy collection install -r requirements.yml
echo "Bootstrap complete."
