#!/bin/bash

# Comprehensive Smart-Update Tag Testing Script

COMMANDS=(
    "templates"
    "php" 
    "api"
    "nodes"
    "scripts"
    "perms"
    "autoinstall"
    "ubuntu"
    "proxmox"
    "nginx"
    "php-fpm"
    "dnsmasq"
    "network"
    "web-all"
    "web-deploy"
    "full-web"
    "foundation"
    "services"
    "expensive"
    "full"
)

echo "=== Smart-Update Comprehensive Tag Testing ==="
echo "Testing $(date)"
echo ""

for cmd in "${COMMANDS[@]}"; do
    echo "--- Testing: $cmd ---"
    
    # Get the tags used by this command
    tags=$(grep -A 2 "$cmd)" scripts/smart-update.sh | grep "run_playbook" | sed 's/.*run_playbook "\([^"]*\)".*/\1/')
    
    if [ -n "$tags" ]; then
        echo "Tags used: $tags"
        
        # Count tasks that will run
        task_count=$(ansible-playbook site-optimized.yml --list-tasks --tags "$tags" 2>/dev/null | grep "TAGS:" | wc -l)
        echo "Tasks to run: $task_count"
        
        # Test dry run for first 20 seconds
        echo "Testing dry run..."
        timeout 20 ./scripts/smart-update.sh "$cmd" --dry-run &>/dev/null
        result=$?
        
        if [ $result -eq 0 ]; then
            echo "Status: ✅ SUCCESS (dry run completed)"
        elif [ $result -eq 124 ]; then
            echo "Status: ⚠️  TIMEOUT (still running after 20s)"
        else
            echo "Status: ❌ FAILED (exit code: $result)"
        fi
    else
        echo "Status: ❌ FAILED (no tags found in script)"
    fi
    
    echo ""
done

echo "=== Testing Complete ==="