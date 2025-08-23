#!/bin/bash
# Simple cluster join script for nodes 2-4

NODE_IP=$1
if [ -z "$NODE_IP" ]; then
    echo "Usage: $0 <node_ip>"
    exit 1
fi

echo "Joining node $NODE_IP to cluster..."

# Get the corosync cluster information 
ssh -o StrictHostKeyChecking=no root@$NODE_IP 'pvecm add 10.10.1.21 --use_ssh' || echo "Join failed"