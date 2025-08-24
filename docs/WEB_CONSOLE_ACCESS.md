# Proxmox Web Console Access Guide

## Status: [SUCCESS] All Web Consoles Are Accessible

The Proxmox web consoles are functioning correctly on all cluster nodes.

## Access URLs

- **Node1**: https://10.10.1.21:8006/
- **Node2**: https://10.10.1.22:8006/
- **Node3**: https://10.10.1.23:8006/
- **Node4**: https://10.10.1.24:8006/

## Login Credentials

- **Username**: `root`
- **Password**: The password you set during Proxmox installation

## Browser Access Notes

### Certificate Warning
You will see a certificate warning in your browser because Proxmox uses self-signed certificates. This is normal and expected. To proceed:

1. **Chrome/Edge**: Click "Advanced" → "Proceed to [IP] (unsafe)"
2. **Firefox**: Click "Advanced" → "Accept the Risk and Continue"
3. **Safari**: Click "Show Details" → "visit this website"

### Network Requirements
Ensure your client machine can reach the management network (10.10.1.0/24) on port 8006.

## Troubleshooting Web Console Access

If you cannot access the web console, check the following:

### 1. Service Status
```bash
ssh root@<node-ip> 'systemctl status pveproxy pvedaemon'
```

### 2. Port Listening
```bash
ssh root@<node-ip> 'ss -tlnp | grep 8006'
```
Should show: `*:8006` (listening on all interfaces)

### 3. Firewall Rules
```bash
ssh root@<node-ip> 'pve-firewall status'
```

If firewall is enabled, ensure port 8006 is allowed:
```bash
ssh root@<node-ip> 'cat /etc/pve/firewall/cluster.fw | grep 8006'
```

### 4. Test Connectivity
From your client machine:
```bash
curl -k https://<node-ip>:8006/
```

### 5. Check Logs
```bash
ssh root@<node-ip> 'journalctl -u pveproxy -n 50'
```

## Common Issues and Solutions

### Issue: "Connection Refused"
**Solution**: Check if pveproxy service is running:
```bash
ssh root@<node-ip> 'systemctl restart pveproxy'
```

### Issue: "Timeout"
**Solution**: Check firewall on both client and server:
```bash
# On node
iptables -L INPUT -n | grep 8006

# On client
telnet <node-ip> 8006
```

### Issue: "Certificate Error Prevents Access"
**Solution**: For testing, use curl with -k flag or add certificate exception in browser.

### Issue: "Login Failed"
**Solution**: 
1. Verify you're using the correct password from installation
2. If password was changed via script, check:
```bash
ssh root@<node-ip> 'grep "root:" /etc/shadow'
```

## Cluster Web Console Features

Once logged in, you can:

1. **Manage all nodes** from any single node's web interface
2. **Create and manage VMs** and containers
3. **Configure storage** (local, Ceph, NFS, etc.)
4. **Set up High Availability** for VMs
5. **Monitor resource usage** across the cluster
6. **Configure networking** and firewall rules
7. **Manage users and permissions**
8. **Set up backup schedules**

## Security Best Practices

1. **Replace self-signed certificates** with proper SSL certificates:
```bash
# Place certificates in:
/etc/pve/nodes/<nodename>/pveproxy-ssl.pem  # Certificate
/etc/pve/nodes/<nodename>/pveproxy-ssl.key  # Private key

# Restart proxy
systemctl restart pveproxy
```

2. **Restrict access** to management network only:
```bash
# In /etc/pve/firewall/cluster.fw
[RULES]
IN ACCEPT -source 10.10.1.0/24 -p tcp -dport 8006  # Only management network
```

3. **Enable two-factor authentication**:
- Go to Datacenter → Permissions → Two Factor
- Configure TOTP or U2F

4. **Create non-root users** for daily operations:
```bash
pveum user add admin@pve
pveum acl modify / -user admin@pve -role PVEAdmin
```

## API Access

The same endpoints serve both the web GUI and API:

```bash
# Get API ticket
curl -k -d "username=root@pam&password=yourpassword" \
  https://10.10.1.21:8006/api2/json/access/ticket

# Use API with ticket
curl -k -H "CSRFPreventionToken: <token>" \
  -H "Cookie: PVEAuthCookie=<ticket>" \
  https://10.10.1.21:8006/api2/json/nodes
```

## Summary

The Proxmox web consoles are fully operational and accessible on all cluster nodes. You can access any node's web interface using:
- URL: `https://<node-ip>:8006/`
- Credentials: `root` / `<installation-password>`

From any single node's interface, you can manage the entire cluster.