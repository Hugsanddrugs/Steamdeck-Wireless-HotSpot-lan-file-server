#!/bin/bash
# SteamOS File Server Post-Update Recovery Script
# Run this after SteamOS updates to restore hotspot functionality

echo "🔧 SteamOS File Server Recovery Script"
echo "======================================"

# Configuration
EXPECTED_DNSMASQ_VERSION="2.90-2"
HOTSPOT_PROFILE_NAME="DeckFileServer"
HOTSPOT_SSID="DeckFileServer"
FILE_SERVER_DIR="/home/deck/FileServer"

# Preflight System Checks
echo "🔍 Running preflight system checks..."
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "❌ Do not run this script as root. Run as normal user."
    exit 1
fi

# Check critical dependencies
for cmd in nmcli systemctl pacman; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "❌ Critical dependency missing: $cmd"
        exit 1
    fi
done
echo "✅ All critical dependencies available"

# Check script directory exists
if [ ! -d "$FILE_SERVER_DIR" ]; then
    echo "❌ FileServer directory not found: $FILE_SERVER_DIR"
    exit 1
fi
echo "✅ FileServer directory exists"

echo ""
echo "📋 Starting system state analysis..."
echo ""

# 1. Check DNSMasq installation
echo "1. Checking DNSMasq..."
if ! pacman -Q dnsmasq &> /dev/null; then
    echo "   ❌ DNSMasq missing - reinstalling..."
    if command -v steamos-readonly &> /dev/null; then
        if steamos-readonly disable; then
            if sudo pacman -S dnsmasq --noconfirm; then
                echo "   ✅ DNSMasq installed successfully"
                steamos-readonly enable
            else
                echo "   ❌ Failed to install DNSMasq"
                exit 1
            fi
        else
            echo "   ❌ Cannot disable read-only mode"
            exit 1
        fi
    else
        echo "   ❌ steamos-readonly command not available"
        exit 1
    fi
else
    CURRENT_DNSMASQ=$(pacman -Q dnsmasq)
    echo "   ✅ DNSMasq present: $CURRENT_DNSMASQ"
fi

# 2. Check systemd-resolved conflict
echo "2. Checking systemd-resolved conflict..."
if systemctl is-active --quiet systemd-resolved; then
    echo "   ⚠️  systemd-resolved active - stopping and disabling..."
    if sudo systemctl stop systemd-resolved && sudo systemctl disable systemd-resolved; then
        echo "   ✅ systemd-resolved stopped and disabled"
    else
        echo "   ❌ Failed to stop systemd-resolved"
    fi
else
    echo "   ✅ systemd-resolved not active"
fi

# 3. Check hotspot profile
echo "3. Checking hotspot profile..."
if nmcli con show | grep -q "$HOTSPOT_PROFILE_NAME"; then
    echo "   ✅ Hotspot profile exists"
    
    # Check if profile has correct SSID
    CURRENT_SSID=$(nmcli -t -f 802-11-wireless.ssid con show "$HOTSPOT_PROFILE_NAME" 2>/dev/null | cut -d: -f2)
    if [ "$CURRENT_SSID" = "$HOTSPOT_SSID" ]; then
        echo "   ✅ Hotspot SSID correct: $CURRENT_SSID"
    else
        echo "   ⚠️  Hotspot SSID mismatch: '$CURRENT_SSID' (expected: '$HOTSPOT_SSID')"
    fi
else
    echo "   ❌ Hotspot profile '$HOTSPOT_PROFILE_NAME' missing"
    echo "   ℹ️  Profile will be recreated on next server startup"
fi

# 4. Check file server dependencies
echo "4. Checking file server environment..."
if cd "$FILE_SERVER_DIR"; then
    # Check virtual environment
    if [ -d "venv" ] && [ -f "venv/bin/python" ]; then
        echo "   ✅ Virtual environment exists"
        # Test Flask availability without activating
        if venv/bin/python -c "import flask, werkzeug" &> /dev/null; then
            echo "   ✅ Flask dependencies available"
        else
            echo "   ❌ Flask dependencies missing"
            echo "   💡 Run: source venv/bin/activate && pip install -r requirements.txt"
        fi
    else
        echo "   ❌ Virtual environment missing or incomplete"
    fi
    
    # Check critical files
    if [ -f "unified_server.py" ] && [ -f "config.py" ]; then
        echo "   ✅ Server files present"
    else
        echo "   ❌ Missing critical server files"
    fi
else
    echo "   ❌ Cannot access FileServer directory"
fi

# 5. Clean up stuck processes SAFELY
echo "5. Cleaning up stuck processes..."
# Check for running server processes
SERVER_PIDS=$(pgrep -f "unified_server.py")
if [ -n "$SERVER_PIDS" ]; then
    echo "   ⚠️  Found running server processes: $SERVER_PIDS"
    if sudo pkill -f "unified_server.py"; then
        echo "   ✅ Killed stuck server processes"
        sleep 2
    else
        echo "   ❌ Failed to kill server processes"
    fi
else
    echo "   ✅ No stuck server processes found"
fi

# Check port 5000 using universal method (no lsof dependency)
if sudo ss -tulpn | grep -q ":5000"; then
    echo "   ⚠️  Port 5000 is in use"
    PORT_PID=$(sudo ss -tulpn | grep ':5000' | awk '{print $7}' | cut -d= -f2 | head -1)
    if [ -n "$PORT_PID" ] && [ "$PORT_PID" -eq "$PORT_PID" ] 2>/dev/null; then
        echo "   ⚠️  Killing process $PORT_PID on port 5000"
        sudo kill -9 "$PORT_PID"
        sleep 1
    else
        echo "   ⚠️  Could not identify process on port 5000"
    fi
else
    echo "   ✅ Port 5000 is clear"
fi

# 6. Restart NetworkManager
echo "6. Restarting NetworkManager..."
if sudo systemctl restart NetworkManager; then
    echo "   ✅ NetworkManager restarted"
    # Give it a moment to stabilize
    sleep 3
else
    echo "   ❌ Failed to restart NetworkManager"
fi

# 7. Final system check
echo ""
echo "7. FINAL SYSTEM STATUS:"
echo "   ===================="
echo "   - DNSMasq: $(pacman -Q dnsmasq 2>/dev/null | head -1 || echo 'MISSING')"
echo "   - systemd-resolved: $(systemctl is-active systemd-resolved 2>/dev/null || echo 'INACTIVE')"
echo "   - Hotspot profile: $(nmcli con show 2>/dev/null | grep -q "$HOTSPOT_PROFILE_NAME" && echo 'EXISTS' || echo 'MISSING')"
echo "   - Port 5000: $(sudo ss -tulpn 2>/dev/null | grep -q ':5000' && echo 'IN USE' || echo 'CLEAR')"
echo "   - FileServer dir: $( [ -d "$FILE_SERVER_DIR" ] && echo 'EXISTS' || echo 'MISSING')"

echo ""
echo "🎯 RECOVERY ACTIONS COMPLETE"
echo "============================"
echo ""
echo "Next steps to start your file server:"
echo "1. cd ~/FileServer"
echo "2. sudo systemctl stop firewalld"
echo "3. source venv/bin/activate"
echo "4. python unified_server.py hotspot"
echo ""
echo "If issues persist, check: ~/FileServer/unified_server.log"
