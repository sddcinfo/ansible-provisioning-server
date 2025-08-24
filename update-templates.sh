#!/bin/bash
# Script to regenerate web templates after configuration changes

echo "Regenerating web templates..."
ansible-playbook -i inventory site.yml --tags www_content

echo "Testing iPXE script..."
curl -s "http://10.10.1.1/index.php?mac=ac:1f:6b:6c:5a:28"

echo ""
echo "Testing answer.toml accessibility..."
curl -s "http://10.10.1.1/sessions/answer.toml" | head -3