#!/bin/bash
# Script to regenerate web templates after configuration changes

echo "Regenerating web templates..."
ansible-playbook -i inventory site.yml --tags web_files_only

echo "Testing iPXE script..."
curl -s "http://10.10.1.1/index.php?mac=ac:1f:6b:6c:5a:76"

echo ""
echo "Testing answer.php API..."
curl -s "http://10.10.1.1/api/answer.php" -H "Content-Type: application/json" -d '{"network_interfaces": [{"link": "eno1", "mac": "ac:1f:6b:6c:5a:76"}]}' | head -5