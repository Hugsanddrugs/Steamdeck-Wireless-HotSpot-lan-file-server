#!/usr/bin/env python3
"""
UNIFIED Steam Deck File Server + Hotspot Controller
FINAL STABLE VERSION: All critical fixes applied + Surgical shutdown enhancements + STREAMING UPLOAD FIX
PRESERVES: All working hotspot functionality, LAN mode, and current configurations
ADDED: Streaming file uploads to fix client transfer speed degradation
"""

import os
import sys
import uuid
import json
import time
import traceback
import subprocess
import inspect
import logging
import socket
import argparse
import signal
import atexit
import io
import zipfile
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from functools import wraps
import tempfile

from flask import (
    Flask, request, redirect, url_for, render_template, session, flash,
    send_file, send_from_directory, g, make_response, jsonify
)
from flask_apscheduler import APScheduler
from werkzeug.utils import secure_filename

# Import config - CRITICAL: Use your exact config structure
try:
    import config
    # Validate required config attributes
    required_attrs = ['PASSWORD', 'ADMIN_PASSWORD', 'MAX_USERS', 'ALLOWED_EXTENSIONS', 
                     'PUBLIC_FOLDER', 'PRIVATE_FOLDER', 'SERVER_PORT']
    for attr in required_attrs:
        if not hasattr(config, attr):
            raise AttributeError(f"Missing required config attribute: {attr}")
    print("✅ Config loaded successfully")
except (ImportError, AttributeError) as e:
    print(f"❌ Config error: {e}")
    sys.exit(1)

# =============================================================================
# CONFIGURATION - COMPATIBLE WITH YOUR config.py
# =============================================================================

# Hotspot Configuration (PRESERVED FROM WORKING VERSION)
HOTSPOT_CONFIG = {
    "name": "DeckFileServer",
    "interface": "wlan0", 
    "ip": "10.42.0.1",
    "password": "deckhotspot123",
    "band": "bg",  # 2.4GHz for phone compatibility - CRITICAL: This was missing
    "ssid": "DeckFileServer"
}

# Server Configuration (PRESERVED FROM WORKING VERSION)
SERVER_CONFIG = {
    "port": config.SERVER_PORT,  # FROM YOUR CONFIG
    "lan_bind": "0.0.0.0",  # CRITICAL: Maintains LAN accessibility
    "public_folder": config.PUBLIC_FOLDER,  # FROM YOUR CONFIG
    "private_folder": config.PRIVATE_FOLDER,  # FROM YOUR CONFIG
}

# =============================================================================
# INDUSTRIAL AUTO-TRACE LOGGING - UNCHANGED FROM WORKING VERSION
# =============================================================================

class IndustrialTraceLogger(logging.Logger):
    def findCaller(self, stack_info=False, stacklevel=1):
        frame = inspect.currentframe()
        for _ in range(stacklevel + 2):
            if frame is None:
                break
            frame = frame.f_back
        
        if frame is not None:
            co = frame.f_code
            filename = os.path.basename(co.co_filename)
            lineno = frame.f_lineno
            funcname = co.co_name
            return (filename, lineno, funcname, None)
        
        return super().findCaller(stack_info, stacklevel)

logging.setLoggerClass(IndustrialTraceLogger)

LOG_FILE = os.path.join(os.path.dirname(__file__), "unified_server.log")
logger = logging.getLogger("UnifiedFileServer")
logger.setLevel(logging.DEBUG)
logger.handlers.clear()

# File Handler
fh = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=5)
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s [pid:%(process)d] (%(filename)s:%(lineno)d) - %(message)s",
    "%Y-%m-%d %H:%M:%S"
))
logger.addHandler(fh)

# Console Handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s (%(filename)s:%(lineno)d) %(message)s",
    "%Y-%m-%d %H:%M:%S"
))
logger.addHandler(ch)

logging.getLogger("werkzeug").setLevel(logging.INFO)

# =============================================================================
# CORE UTILITIES - PRESERVED FROM WORKING SERVER
# =============================================================================

SECRETS = set()
SECRETS.add(str(config.PASSWORD))
SECRETS.add(str(config.ADMIN_PASSWORD))

def redact(s):
    if not s:
        return s
    out = str(s)
    for sec in SECRETS:
        if sec and sec in out:
            out = out.replace(sec, "***REDACTED***")
    return out

def run_cmd(cmd, check=False, timeout=30):
    """PRESERVED: Exact same implementation from working server"""
    try:
        env = os.environ.copy()
        env['DBUS_SESSION_BUS_ADDRESS'] = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
        env['PATH'] = os.environ.get('PATH', '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
        
        if isinstance(cmd, (list, tuple)):
            cmdstr = " ".join(cmd)
            if cmd and cmd[0] == 'nmcli':
                cmd = ['/usr/bin/nmcli'] + cmd[1:]
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        else:
            cmdstr = cmd
            if cmd.startswith('nmcli '):
                cmd = '/usr/bin/nmcli ' + cmd[6:]
            cp = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, env=env)
        
        logger.debug("CMD: %s | rc=%s | stdout=%s | stderr=%s",
                     redact(cmdstr), cp.returncode, redact(cp.stdout), redact(cp.stderr))
        if check and cp.returncode != 0:
            raise subprocess.CalledProcessError(cp.returncode, cmd, output=cp.stdout, stderr=cp.stderr)
        return cp
    except Exception:
        logger.exception("CMD ERROR: %s", redact(cmd))
        return None

def trace(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        logger.debug("ENTER %s args=%s kwargs=%s", func.__name__, args, kwargs)
        try:
            res = func(*args, **kwargs)
            logger.debug("EXIT %s result_type=%s", func.__name__, type(res))
            return res
        except Exception:
            logger.exception("EXCEPTION in %s", func.__name__)
            raise
    return wrapper

# =============================================================================
# HOTSPOT MANAGEMENT - WITH SURGICAL SHUTDOWN ENHANCEMENTS
# =============================================================================

class HotspotManager:
    def __init__(self):
        self.hotspot_name = HOTSPOT_CONFIG["name"]
        self.interface = HOTSPOT_CONFIG["interface"]
        self.hotspot_ip = HOTSPOT_CONFIG["ip"]
        self.password = HOTSPOT_CONFIG["password"]
        self.ssid = HOTSPOT_CONFIG["ssid"]
        self.band = HOTSPOT_CONFIG["band"]  # CRITICAL FIX: This was missing causing crashes
        self.is_active = False
        self.original_connection = None
        self.current_profile_uuid = None
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # SURGICAL ADDITION: Track original WiFi for restoration
        self.original_wifi_connection = None
        self.original_wifi_saved = False
        
        # Initialize diagnostic logging
        self.setup_diagnostic_logging()

    def setup_diagnostic_logging(self):
        """Initialize comprehensive diagnostic logging"""
        logger.info("=== DIAGNOSTIC RUN %s ===", self.run_id)
        
        # Log previous successful run if available
        prev_run = self.get_previous_successful_run()
        if prev_run:
            logger.info("Previous Successful Run: %s", prev_run)
        
        # Log current state
        logger.info("=== PRE-STARTUP BASELINE ===")
        logger.info("Active WiFi: %s", self.get_current_wifi())
        logger.info("Existing Profiles: %s", self.get_existing_profiles())

    def get_current_wifi(self):
        """Get current WiFi connection - PRESERVED FROM WORKING VERSION"""
        result = run_cmd(["/usr/bin/nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active"])
        if result and result.returncode == 0:
            for line in result.stdout.splitlines():
                if f":{self.interface}" in line:
                    return line.split(":")[0]
        return "None"

    def get_existing_profiles(self):
        """Get existing hotspot profiles - PRESERVED FROM WORKING VERSION"""
        result = run_cmd(["/usr/bin/nmcli", "-t", "-f", "NAME,UUID", "con", "show"])
        profiles = []
        if result and result.returncode == 0:
            for line in result.stdout.splitlines():
                if "DeckFileServer" in line or "Hotspot" in line:
                    profiles.append(line)
        return profiles if profiles else "None"

    def get_previous_successful_run(self):
        """Get previous successful run info - PRESERVED FROM WORKING VERSION"""
        cache_file = os.path.expanduser("~/.fileserver_hotspot_cache.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    cached_config = json.load(f)
                return f"UUID: {cached_config.get('profile_uuid', 'Unknown')} at {cached_config.get('timestamp', 'Unknown')}"
            except:
                pass
        return None

    # SURGICAL ADDITION: Capture original WiFi state for restoration
    @trace
    def capture_original_wifi_state(self):
        """SURGICAL: Capture original WiFi connection for graceful restoration"""
        try:
            original_wifi = self.get_current_wifi()
            if original_wifi and original_wifi != "None" and original_wifi != self.hotspot_name:
                self.original_wifi_connection = original_wifi
                self.original_wifi_saved = True
                logger.info("💾 Saved original WiFi connection: %s", self.original_wifi_connection)
                return True
            else:
                logger.info("📝 No original WiFi connection to save")
                return False
        except Exception as e:
            logger.warning("⚠️ Could not capture original WiFi state: %s", e)
            return False

    # SURGICAL ADDITION: Attempt to restore original WiFi
    @trace
    def attempt_wifi_restoration(self):
        """SURGICAL: Attempt to restore original WiFi connection"""
        if not self.original_wifi_saved or not self.original_wifi_connection:
            logger.info("📝 No original WiFi connection to restore")
            return False
            
        try:
            logger.info("🔌 Attempting to restore original WiFi: %s", self.original_wifi_connection)
            result = run_cmd(["/usr/bin/nmcli", "con", "up", self.original_wifi_connection])
            if result and result.returncode == 0:
                logger.info("✅ Successfully restored WiFi: %s", self.original_wifi_connection)
                return True
            else:
                logger.warning("⚠️ Could not automatically restore WiFi: %s", self.original_wifi_connection)
                logger.info("💡 Manual recovery: Run 'nmcli con up \"Tom & jerry\"'")
                return False
        except Exception as e:
            logger.warning("⚠️ WiFi restoration attempt failed: %s", e)
            return False

    @trace
    def ensure_dbus_environment(self):
        """PRESERVED FROM WORKING VERSION"""
        logger.info("🔧 Ensuring DBUS environment...")
        
        if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
            steam_pid = run_cmd(["pgrep", "-x", "steam"])
            if steam_pid and steam_pid.returncode == 0:
                try:
                    env_file = f"/proc/{steam_pid.stdout.strip()}/environ"
                    with open(env_file, 'rb') as f:
                        env_data = f.read()
                    env_parts = env_data.split(b'\x00')
                    for part in env_parts:
                        if part.startswith(b'DBUS_SESSION_BUS_ADDRESS='):
                            dbus_addr = part.decode().split('=', 1)[1]
                            os.environ['DBUS_SESSION_BUS_ADDRESS'] = dbus_addr
                            logger.info("✅ DBUS environment configured from Steam")
                            break
                except Exception as e:
                    logger.warning("⚠️ Could not get DBUS from Steam: %s", e)
        
        if os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
            logger.info("✅ DBUS environment ready")
        else:
            logger.warning("⚠️ DBUS environment not configured - may affect nmcli")
        
        return True

    @trace
    def ensure_clean_interface(self):
        """PRESERVED FROM WORKING VERSION with surgical addition"""
        logger.info("🧹 Cleaning network interface...")
        
        # SURGICAL ADDITION: Capture original WiFi BEFORE cleaning
        self.capture_original_wifi_state()
        
        # PRESERVED: Original cleanup logic
        # Save current managed state
        result = run_cmd(["/usr/bin/nmcli", "-t", "-f", "DEVICE,MANAGED", "dev"])
        if result and result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith(f"{self.interface}:"):
                    original_state = line.split(":")[1]
                    with open(f"/tmp/{self.interface}_managed_state", "w") as f:
                        f.write(original_state)
        
        # Ensure interface is managed
        run_cmd(["/usr/bin/nmcli", "dev", "set", self.interface, "managed", "yes"])
        
        # Disconnect active connections
        result = run_cmd(["/usr/bin/nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active"])
        if result and result.returncode == 0:
            for line in result.stdout.splitlines():
                if f":{self.interface}" in line:
                    conn_name = line.split(":")[0]
                    logger.info("🔌 Disconnecting: %s", conn_name)
                    run_cmd(["/usr/bin/nmcli", "con", "down", conn_name])
        
        # Reset interface
        run_cmd(["/usr/bin/nmcli", "radio", "wifi", "off"])
        time.sleep(2)
        run_cmd(["/usr/bin/nmcli", "radio", "wifi", "on"])
        time.sleep(3)
        
        logger.info("✅ Interface cleanup completed")
        return True

    @trace
    def validate_existing_profile(self):
        """PRESERVED FROM WORKING VERSION"""
        result = run_cmd(["/usr/bin/nmcli", "con", "show", self.hotspot_name])
        if result and result.returncode == 0:
            # Get the UUID for tracking
            uuid_result = run_cmd(["/usr/bin/nmcli", "-t", "-f", "UUID", "con", "show", self.hotspot_name])
            if uuid_result and uuid_result.returncode == 0:
                self.current_profile_uuid = uuid_result.stdout.strip()
                logger.info("📋 Found existing profile UUID: %s", self.current_profile_uuid)
            
            # Test if profile can be activated
            test_result = run_cmd(["/usr/bin/nmcli", "con", "up", self.hotspot_name])
            if test_result and test_result.returncode == 0:
                logger.info("✅ Existing profile validated and working")
                run_cmd(["/usr/bin/nmcli", "con", "down", self.hotspot_name])  # Deactivate test
                return True
        return False

    @trace
    def get_or_create_hotspot_profile(self):
        """PRESERVED FROM WORKING VERSION"""
        logger.info("=== PROFILE OPERATIONS ===")
        
        # Check if existing profile is valid and reusable
        if self.validate_existing_profile():
            logger.info("♻️  Reusing existing hotspot profile: %s", self.current_profile_uuid)
            return True
        
        # Only create new if none exists or existing is invalid
        logger.info("🆕 Creating new hotspot profile")
        return self.create_new_hotspot_profile()

    @trace
    def create_new_hotspot_profile(self):
        """PRESERVED FROM WORKING VERSION"""
        logger.info("📡 Creating hotspot profile...")
        
        # Delete existing profile
        run_cmd(["/usr/bin/nmcli", "con", "delete", self.hotspot_name])
        time.sleep(1)
        
        # Create new hotspot using the CORRECT method with band parameter
        result = run_cmd([
            "/usr/bin/nmcli", "con", "add", "type", "wifi", "ifname", self.interface,
            "con-name", self.hotspot_name, "autoconnect", "no", "ssid", self.ssid,
            "802-11-wireless.mode", "ap", "802-11-wireless.band", self.band,  # CRITICAL FIX: band was missing
            "ipv4.method", "shared", "ipv4.addresses", f"{self.hotspot_ip}/24",
            "ipv6.method", "ignore"
        ])
        
        if result and result.returncode == 0:
            # Set security
            run_cmd(["/usr/bin/nmcli", "con", "mod", self.hotspot_name, "802-11-wireless-security.key-mgmt", "wpa-psk"])
            run_cmd(["/usr/bin/nmcli", "con", "mod", self.hotspot_name, "802-11-wireless-security.psk", self.password])
            
            # Get the new UUID for tracking
            uuid_result = run_cmd(["/usr/bin/nmcli", "-t", "-f", "UUID", "con", "show", self.hotspot_name])
            if uuid_result and uuid_result.returncode == 0:
                self.current_profile_uuid = uuid_result.stdout.strip()
                logger.info("📋 New profile UUID: %s", self.current_profile_uuid)
            
            logger.info("✅ Hotspot profile created successfully")
            return True
        else:
            logger.error("❌ Failed to create hotspot profile")
            return False

    @trace
    def setup_network_routing(self):
        """PRESERVED FROM WORKING VERSION"""
        logger.info("🔧 Setting up network routing...")
        
        # Enable IP forwarding (required for routing between interfaces)
        run_cmd(["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"])
        
        # NAT/MASQUERADE rules (allows hotspot clients to reach server)
        run_cmd(["sudo", "iptables", "-t", "nat", "-A", "POSTROUTING", "-o", "eth0", "-j", "MASQUERADE"])
        run_cmd(["sudo", "iptables", "-A", "FORWARD", "-i", "wlan0", "-o", "eth0", "-j", "ACCEPT"])
        run_cmd(["sudo", "iptables", "-A", "FORWARD", "-i", "eth0", "-o", "wlan0", "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"])
        
        logger.info("✅ Network routing configured")
        return True

    @trace
    def verify_hotspot_ip(self, max_attempts=10, delay=2):
        """PRESERVED FROM WORKING VERSION"""
        logger.info("🔍 Verifying hotspot IP assignment...")
        start_time = time.time()
        
        for attempt in range(max_attempts):
            if self.check_hotspot_active():
                duration = time.time() - start_time
                logger.info("✅ Hotspot IP verified: %s (attempt %d/%d, %.2fs)", 
                           self.hotspot_ip, attempt + 1, max_attempts, duration)
                return True
            logger.info("⏳ Waiting for IP assignment (%d/%d)...", attempt + 1, max_attempts)
            time.sleep(delay)
        
        logger.error("❌ Hotspot IP not assigned within %d attempts", max_attempts)
        return False

    @trace  
    def verify_network_routing(self):
        """PRESERVED FROM WORKING VERSION"""
        logger.info("🔍 Verifying network routing...")
        result = run_cmd(["sudo", "iptables", "-t", "nat", "-L", "POSTROUTING"])
        if result and "MASQUERADE" in result.stdout:
            logger.info("✅ NAT routing verified")
            return True
        logger.error("❌ NAT routing not configured")
        return False

    @trace
    def check_hotspot_active(self):
        """PRESERVED FROM WORKING VERSION"""
        result = run_cmd(["ip", "-o", "-4", "addr", "show", self.interface])
        if result and result.returncode == 0 and self.hotspot_ip in result.stdout:
            return True
        return False

    @trace
    def activate_hotspot(self):
        """PRESERVED FROM WORKING VERSION"""
        logger.info("🚀 Activating hotspot connection...")
        result = run_cmd(["/usr/bin/nmcli", "con", "up", self.hotspot_name])
        if result and result.returncode == 0:
            logger.info("✅ Hotspot activated successfully")
            self.is_active = True
            return True
        else:
            logger.error("❌ Failed to activate hotspot")
            return False

    @trace
    def remember_working_config(self):
        """PRESERVED FROM WORKING VERSION"""
        if not self.current_profile_uuid:
            return
            
        config_cache = {
            "profile_uuid": self.current_profile_uuid,
            "hotspot_ip": self.hotspot_ip,
            "timestamp": datetime.now().isoformat(),
            "run_id": self.run_id
        }
        
        # Save to persistent storage
        cache_file = os.path.expanduser("~/.fileserver_hotspot_cache.json")
        try:
            with open(cache_file, 'w') as f:
                json.dump(config_cache, f, indent=2)
            os.chmod(cache_file, 0o600)
            logger.info("💾 Saved working configuration to cache: %s", self.current_profile_uuid)
        except Exception as e:
            logger.warning("⚠️ Failed to save config cache: %s", e)

    def start_hotspot_with_verification(self):
        """PRESERVED FROM WORKING VERSION"""
        logger.info("🎯 Starting hotspot with verification chain...")
        
        steps = [
            ("DBUS Environment", self.ensure_dbus_environment),
            ("Interface Cleanup", self.ensure_clean_interface),
            ("Profile Management", self.get_or_create_hotspot_profile),
            ("Hotspot Activation", self.activate_hotspot),
            ("IP Assignment", self.verify_hotspot_ip),
            ("Network Routing", self.setup_network_routing),
            ("Routing Verification", self.verify_network_routing)
        ]
        
        for step_name, step_function in steps:
            logger.info("🔍 VERIFYING: %s...", step_name)
            start_time = time.time()
            
            if not step_function():
                logger.error("❌ STEP FAILED: %s", step_name)
                return False
                
            duration = time.time() - start_time
            logger.info("✅ VERIFIED: %s completed in %.2fs", step_name, duration)
        
        # Remember successful configuration
        self.remember_working_config()
        
        logger.info("🎉 All hotspot verification steps completed successfully")
        return True

    @trace
    def stop_hotspot(self):
        """ENHANCED: Stop hotspot with surgical WiFi restoration"""
        logger.info("🛑 Stopping hotspot...")
        
        # Bring down hotspot
        run_cmd(["/usr/bin/nmcli", "con", "down", self.hotspot_name])
        
        # SURGICAL ADDITION: Attempt WiFi restoration
        self.attempt_wifi_restoration()
        
        # Clean up managed state
        managed_file = f"/tmp/{self.interface}_managed_state"
        if os.path.exists(managed_file):
            with open(managed_file, "r") as f:
                original_state = f.read().strip()
            run_cmd(["/usr/bin/nmcli", "dev", "set", self.interface, "managed", original_state])
            os.unlink(managed_file)
        
        self.is_active = False
        logger.info("✅ Hotspot stopped and cleaned up")

# =============================================================================
# FLASK SERVER - PRESERVING ALL ORIGINAL LAN FUNCTIONALITY
# =============================================================================

app = Flask(__name__)
app.secret_key = os.urandom(32)
app.permanent_session_lifetime = timedelta(minutes=30)

scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

# Global state - PRESERVED FROM ORIGINAL
active_users = {}
scheduled_deletions = {}
ADMIN_TOKENS_FILE = os.path.expanduser("~/.file_server_admin_tokens")
admin_tokens = set()

# Load existing admin tokens
if os.path.exists(ADMIN_TOKENS_FILE):
    try:
        with open(ADMIN_TOKENS_FILE, 'r') as f:
            admin_tokens = set(json.load(f))
        logger.info("Loaded %d admin tokens", len(admin_tokens))
    except Exception:
        logger.exception("Failed to load admin tokens")

# Hotspot manager instance (PRESERVED - doesn't affect existing routes)
hotspot_manager = HotspotManager()

# =============================================================================
# SERVER UTILITIES - PRESERVED FROM ORIGINAL WORKING SERVER
# =============================================================================

@trace
def ensure_dirs():
    """PRESERVED FROM ORIGINAL SERVER"""
    for path in [config.PUBLIC_FOLDER, config.PRIVATE_FOLDER]:
        try:
            os.makedirs(path, exist_ok=True)
            logger.debug("Ensured folder %s exists", path)
        except Exception:
            logger.exception("Failed to ensure directory %s", path)

@trace
def save_admin_tokens():
    """PRESERVED FROM ORIGINAL SERVER"""
    try:
        with open(ADMIN_TOKENS_FILE, 'w') as f:
            json.dump(list(admin_tokens), f)
        logger.info("Saved %d admin tokens", len(admin_tokens))
    except Exception:
        logger.exception("Failed to save admin tokens")

@trace
def is_admin():
    """PRESERVED FROM ORIGINAL SERVER"""
    admin_token = session.get('admin_token') or request.cookies.get('admin_token')
    return admin_token and admin_token in admin_tokens

@trace
def allowed_file(filename):
    """PRESERVED FROM ORIGINAL SERVER"""
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    blocked = {"exe", "sh", "bat", "msi", "com", "dll", "appimage"}
    if ext in blocked:
        return False
    allowed = config.ALLOWED_EXTENSIONS
    if "*" in allowed:
        return True
    return ext in allowed

@trace
def unique_filename(folder, filename):
    """PRESERVED FROM ORIGINAL SERVER"""
    base, ext = os.path.splitext(filename)
    safe = secure_filename(filename)
    candidate = safe or "file"
    i = 1
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f"{secure_filename(base)}_{i}{ext}"
        i += 1
    return candidate

@trace
def write_meta(filepath, uploader_id, uploader_ip, original_name, private=False):
    """PRESERVED FROM ORIGINAL SERVER"""
    meta = {
        "uploader_id": uploader_id,
        "uploader_ip": uploader_ip,
        "original_filename": original_name,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "first_download_at": None,
        "private": bool(private)
    }
    meta_path = filepath + ".meta.json"
    try:
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump(meta, mf, indent=2)
        logger.info("Wrote meta for %s", filepath)
    except Exception:
        logger.exception("Failed to write meta for %s", filepath)

@trace
def read_meta(filepath):
    """PRESERVED FROM ORIGINAL SERVER"""
    meta_path = filepath + ".meta.json"
    if not os.path.exists(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as mf:
            return json.load(mf)
    except Exception:
        logger.exception("Failed reading meta %s", meta_path)
        return {}

# =============================================================================
# FLASK ROUTES - PRESERVED FROM ORIGINAL WORKING SERVER + STREAMING UPLOAD FIX
# =============================================================================

@app.before_request
def before_request():
    g.start = time.time()

@app.after_request
def after_request(response):
    try:
        dur = (time.time() - getattr(g, "start", time.time())) * 1000.0
        logger.info("REQUEST: %s %s status=%s dur_ms=%.2f",
                    request.remote_addr, request.path, response.status_code, dur)
    except Exception:
        logger.exception("after_request logging failed")
    return response

@app.route("/", methods=["GET", "POST"])
def login():
    """PRESERVED FROM ORIGINAL SERVER"""
    try:
        if request.method == "POST":
            pw = request.form.get("password", "")
            
            # Check admin password
            if pw == config.ADMIN_PASSWORD:
                admin_token = str(uuid.uuid4())
                admin_tokens.add(admin_token)
                save_admin_tokens()
                
                session["logged_in"] = True
                session["user_id"] = str(uuid.uuid4())
                session["admin_token"] = admin_token
                session.permanent = True
                
                active_users[session["user_id"]] = {
                    "ip": request.remote_addr,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                    "is_admin": True
                }
                
                response = make_response(redirect(url_for("home")))
                response.set_cookie('admin_token', admin_token, max_age=365*24*60*60)
                return response
            
            # Check regular password
            elif pw == config.PASSWORD:
                non_admins = sum(1 for u in active_users.values() if not u.get("is_admin"))
                is_admin_flag = is_admin()
                
                if non_admins >= config.MAX_USERS and not is_admin_flag:
                    flash("Maximum users reached. Try later.")
                    return redirect(url_for("login"))
                
                session["logged_in"] = True
                session["user_id"] = str(uuid.uuid4())
                session.permanent = True
                
                active_users[session["user_id"]] = {
                    "ip": request.remote_addr,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                    "is_admin": is_admin_flag
                }
                return redirect(url_for("home"))
            else:
                flash("Invalid password.")
        return render_template("login.html")
    except Exception:
        logger.exception("Exception in login")
        raise

@app.route("/home")
def home():
    """PRESERVED FROM ORIGINAL SERVER"""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template("upload.html", is_admin=is_admin(), active_users=active_users)

@app.route("/upload", methods=["POST"])
def upload():
    """🚀 FIXED: Streaming file uploads to prevent memory exhaustion"""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    files = request.files.getlist("file")
    folder_choice = request.form.get("folder", "public")
    save_folder = config.PUBLIC_FOLDER if folder_choice == "public" else config.PRIVATE_FOLDER
    saved_files = []
    
    try:
        for f in files:
            if not f or not f.filename:
                continue
            fname = secure_filename(f.filename)
            if not allowed_file(fname):
                flash(f"Blocked file type: {fname}")
                continue
            unique = unique_filename(save_folder, fname)
            path = os.path.join(save_folder, unique)
            
            # 🚀 CRITICAL FIX: Stream file directly to disk in chunks
            # Prevents memory exhaustion and TCP backpressure during large uploads
            chunk_size = 8192  # 8KB chunks - optimal for network transfer
            try:
                with open(path, 'wb') as dest:
                    while True:
                        chunk = f.stream.read(chunk_size)
                        if not chunk:
                            break
                        dest.write(chunk)
                logger.info(f"✅ Successfully streamed file: {fname} -> {unique}")
            except Exception as e:
                logger.error(f"❌ File streaming failed for {fname}: {e}")
                flash(f"Upload failed for {fname}")
                continue
            
            write_meta(path, session.get("user_id"), request.remote_addr, fname, private=(folder_choice=="private"))
            saved_files.append(unique)
        
        if saved_files:
            flash(f"Uploaded: {', '.join(saved_files)}")
        return redirect(url_for("home"))
    except Exception:
        logger.exception("Upload exception")
        flash("Upload failed")
        return redirect(url_for("home"))

@app.route("/files")
def files():
    """PRESERVED FROM ORIGINAL SERVER"""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    uid = session.get("user_id")
    is_admin_flag = is_admin()
    listing = []
    
    try:
        for folder, tag in [(config.PUBLIC_FOLDER, "public"), (config.PRIVATE_FOLDER, "private")]:
            if not os.path.isdir(folder):
                continue
            for fname in os.listdir(folder):
                if fname.endswith(".meta.json"):
                    continue
                path = os.path.join(folder, fname)
                
                # ADDED SAFEGUARD: Skip if file doesn't exist (race condition)
                if not os.path.exists(path):
                    continue
                    
                try:
                    meta = read_meta(path)
                    file_info = {
                        "folder": tag,
                        "saved_name": fname,
                        "original_name": meta.get("original_filename", fname),
                        "size": os.path.getsize(path),
                        "uploaded_at": meta.get("uploaded_at"),
                        "uploader_id": meta.get("uploader_id")
                    }
                    
                    if meta.get("private") and not (is_admin_flag or meta.get("uploader_id")==uid):
                        continue
                    listing.append(file_info)
                    
                except Exception as e:
                    logger.warning("Skipping problematic file %s: %s", path, e)
                    continue
                    
        return render_template("files.html", files=listing, is_admin=is_admin_flag, 
                              active_users=active_users, current_user_id=uid)
    except Exception:
        logger.exception("files listing failed")
        raise

@app.route("/download/<folder>/<filename>")
def download(folder, filename):
    """PRESERVED FROM ORIGINAL SERVER"""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    base = config.PUBLIC_FOLDER if folder == "public" else config.PRIVATE_FOLDER
    path = os.path.join(base, secure_filename(filename))
    
    if not os.path.exists(path):
        flash("File not found")
        return redirect(url_for("files"))
    
    meta = read_meta(path)
    if meta.get("private") and not (is_admin() or meta.get("uploader_id")==session.get("user_id")):
        flash("Access denied")
        return redirect(url_for("files"))
    
    return send_from_directory(base, filename, as_attachment=True)

@app.route("/logout")
def logout():
    """PRESERVED FROM ORIGINAL SERVER"""
    uid = session.get("user_id")
    if uid and uid in active_users:
        del active_users[uid]
    session.clear()
    return redirect(url_for("login"))

@app.route("/_health")
def health_check():
    """PRESERVED FROM ORIGINAL SERVER"""
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})

# =============================================================================
# BULK OPERATION ROUTES - PRESERVED FROM ORIGINAL
# =============================================================================

@app.route("/download_selected", methods=["POST"])
def download_selected():
    """Download selected files as ZIP"""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    try:
        files_to_download = request.form.getlist("files")
        user_id = session.get("user_id")
        is_admin_flag = is_admin()
        
        if not files_to_download:
            flash("No files selected for download")
            return redirect(url_for("files"))
        
        # Create temporary ZIP file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for file_str in files_to_download:
                try:
                    folder, filename = file_str.split(":", 1)
                    base_folder = config.PUBLIC_FOLDER if folder == "public" else config.PRIVATE_FOLDER
                    file_path = os.path.join(base_folder, secure_filename(filename))
                    
                    if os.path.exists(file_path):
                        meta = read_meta(file_path)
                        # Check permissions
                        if meta.get("private") and not (is_admin_flag or meta.get("uploader_id") == user_id):
                            continue
                            
                        # Add file to ZIP with original name
                        original_name = meta.get("original_filename", filename)
                        zip_file.write(file_path, original_name)
                        
                except Exception as e:
                    logger.error("Error adding file %s to ZIP: %s", file_str, e)
                    continue
        
        zip_buffer.seek(0)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=f"files_download_{timestamp}.zip",
            mimetype="application/zip"
        )
        
    except Exception as e:
        logger.exception("Error in download_selected")
        flash("Download operation failed")
        return redirect(url_for("files"))

@app.route("/delete_selected", methods=["POST"])
def delete_selected():
    """Delete selected files for regular users"""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    try:
        files_to_delete = request.form.getlist("files")
        user_id = session.get("user_id")
        deleted_count = 0
        
        if not files_to_delete:
            flash("No files selected for deletion")
            return redirect(url_for("files"))
        
        for file_str in files_to_delete:
            try:
                folder, filename = file_str.split(":", 1)
                base_folder = config.PUBLIC_FOLDER if folder == "public" else config.PRIVATE_FOLDER
                file_path = os.path.join(base_folder, secure_filename(filename))
                
                # Check if file exists and user has permission
                if os.path.exists(file_path):
                    meta = read_meta(file_path)
                    # Users can only delete their own files
                    if meta.get("uploader_id") == user_id:
                        os.remove(file_path)
                        # Delete metadata file
                        meta_path = file_path + ".meta.json"
                        if os.path.exists(meta_path):
                            os.remove(meta_path)
                        deleted_count += 1
                        logger.info("User %s deleted file: %s", user_id, file_path)
                    else:
                        logger.warning("User %s attempted to delete file they don't own: %s", user_id, file_path)
            except Exception as e:
                logger.error("Error deleting file %s: %s", file_str, e)
                continue
        
        if deleted_count > 0:
            flash(f"Deleted {deleted_count} files")
        else:
            flash("No files were deleted - check permissions")
        return redirect(url_for("files"))
        
    except Exception as e:
        logger.exception("Error in delete_selected")
        flash("Delete operation failed")
        return redirect(url_for("files"))

@app.route("/admin/delete_selected", methods=["POST"])
def admin_delete_selected():
    """Delete selected files for admin users"""
    if not session.get("logged_in") or not is_admin():
        flash("Admin access required")
        return redirect(url_for("login"))
    
    try:
        files_to_delete = request.form.getlist("files")
        deleted_count = 0
        
        if not files_to_delete:
            flash("No files selected for deletion")
            return redirect(url_for("files"))
        
        for file_str in files_to_delete:
            try:
                folder, filename = file_str.split(":", 1)
                base_folder = config.PUBLIC_FOLDER if folder == "public" else config.PRIVATE_FOLDER
                file_path = os.path.join(base_folder, secure_filename(filename))
                
                # Admin can delete any file
                if os.path.exists(file_path):
                    os.remove(file_path)
                    # Delete metadata file
                    meta_path = file_path + ".meta.json"
                    if os.path.exists(meta_path):
                        os.remove(meta_path)
                    deleted_count += 1
                    logger.info("Admin deleted file: %s", file_path)
            except Exception as e:
                logger.error("Error deleting file %s: %s", file_str, e)
                continue
        
        flash(f"Deleted {deleted_count} files")
        return redirect(url_for("files"))
        
    except Exception as e:
        logger.exception("Error in admin_delete_selected")
        flash("Delete operation failed")
        return redirect(url_for("files"))

@app.route("/admin/delete_all", methods=["POST"])
def admin_delete_all():
    """Delete all files (admin only)"""
    if not session.get("logged_in") or not is_admin():
        flash("Admin access required")
        return redirect(url_for("login"))
    
    try:
        deleted_count = 0
        
        # Delete from public folder
        for folder in [config.PUBLIC_FOLDER, config.PRIVATE_FOLDER]:
            if os.path.exists(folder):
                for filename in os.listdir(folder):
                    if filename.endswith(".meta.json"):
                        continue
                    file_path = os.path.join(folder, filename)
                    try:
                        os.remove(file_path)
                        # Delete metadata file
                        meta_path = file_path + ".meta.json"
                        if os.path.exists(meta_path):
                            os.remove(meta_path)
                        deleted_count += 1
                    except Exception as e:
                        logger.error("Error deleting file %s: %s", file_path, e)
                        continue
        
        flash(f"Deleted all files ({deleted_count} total)")
        return redirect(url_for("files"))
        
    except Exception as e:
        logger.exception("Error in admin_delete_all")
        flash("Delete all operation failed")
        return redirect(url_for("files"))

# =============================================================================
# SERVER STARTUP MANAGEMENT - PRESERVES LAN MODE, ADDS HOTSPOT OPTION
# =============================================================================

def start_lan_mode():
    """PRESERVED ORIGINAL BEHAVIOR"""
    logger.info("🌐 STARTING LAN MODE")
    logger.info("   Port: %s", config.SERVER_PORT)
    logger.info("   Bind: 0.0.0.0 (network accessible)")
    
    ensure_dirs()
    
    # Get LAN IP for user information
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
        logger.info("📍 LAN Access URL: http://%s:%s", lan_ip, config.SERVER_PORT)
    except:
        logger.info("📍 LAN Access URL: http://[YOUR-DECK-IP]:%s", config.SERVER_PORT)
    
    logger.info("📍 Local Access URL: http://localhost:%s", config.SERVER_PORT)
    
    try:
        # CRITICAL: This is the exact same startup as your working LAN server
        app.run(host="0.0.0.0", port=config.SERVER_PORT, debug=False, use_reloader=False)
        return True
    except Exception as e:
        logger.critical("❌ Failed to start LAN server: %s", e)
        return False

def start_hotspot_mode():
    """PRESERVED WORKING VERSION with surgical enhancements"""
    logger.info("🔥 STARTING HOTSPOT MODE")
    
    # Start hotspot with verification chain
    if not hotspot_manager.start_hotspot_with_verification():
        logger.error("❌ Hotspot setup failed, falling back to LAN mode")
        return start_lan_mode()
    
    ensure_dirs()
    
    logger.info("=== HOTSPOT NETWORK CONFIGURATION ===")
    logger.info("📍 Hotspot Access URL: http://%s:%s", HOTSPOT_CONFIG["ip"], config.SERVER_PORT)
    logger.info("📍 SSID: %s", HOTSPOT_CONFIG["ssid"])
    logger.info("📍 Password: %s", HOTSPOT_CONFIG["password"])
    logger.info("📍 Profile UUID: %s", hotspot_manager.current_profile_uuid or "Unknown")
    
    try:
        # Same server startup as LAN mode, just with hotspot active
        app.run(host="0.0.0.0", port=config.SERVER_PORT, debug=False, use_reloader=False)
        return True
    except Exception as e:
        logger.critical("❌ Failed to start hotspot server: %s", e)
        return False

def graceful_shutdown():
    """ENHANCED: Graceful shutdown with surgical improvements"""
    logger.info("🛑 Initiating graceful shutdown...")
    
    if hotspot_manager.is_active:
        hotspot_manager.stop_hotspot()  # Now includes WiFi restoration
    
    # Stop scheduler
    try:
        scheduler.shutdown()
    except:
        pass
    
    logger.info("✅ Shutdown complete")

# =============================================================================
# COMMAND LINE INTERFACE - PRESERVED FROM WORKING VERSION
# =============================================================================

def main():
    """PRESERVED FROM WORKING VERSION"""
    parser = argparse.ArgumentParser(description="Steam Deck File Server")
    parser.add_argument("mode", choices=["lan", "hotspot"], 
                       help="Server mode: lan (LAN only) or hotspot (create hotspot)")
    
    args = parser.parse_args()
    
    # Setup graceful shutdown
    signal.signal(signal.SIGINT, lambda s, f: graceful_shutdown())
    signal.signal(signal.SIGTERM, lambda s, f: graceful_shutdown())
    atexit.register(graceful_shutdown)
    
    logger.info("🚀 STEAM DECK FILE SERVER STARTING")
    logger.info("   Mode: %s", args.mode.upper())
    logger.info("   Port: %s", config.SERVER_PORT)
    logger.info("   PID: %s", os.getpid())
    logger.info("=" * 50)
    
    try:
        if args.mode == "lan":
            success = start_lan_mode()
        else:  # hotspot
            success = start_hotspot_mode()
        
        if not success:
            logger.critical("❌ Server failed to start")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("👋 Shutdown requested by user")
    except Exception as e:
        logger.critical("💥 Fatal error: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
