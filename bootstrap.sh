#!/bin/bash
# This script prepares the control node to run the provisioning server playbook.
set -e
echo "--- Installing Ansible and dependencies ---"
sudo apt-get update
sudo apt-get install -y ansible git
echo "--- Installing required Ansible collections ---"
ansible-galaxy collection install -r requirements.yml
echo "Bootstrap complete."
