# Steamdeck-Wireless-HotSpot-lan-file-server
Steamdeck Wireless HotSpot/lan file server running locally on steam deck self hosted
 including timestamps for all relevant events.
FileServer + Hotspot Project: Complete Final Overview
Project Overview
Objective:
Create a wireless file-sharing system on Steam Deck allowing phones to connect via hotspot and access a Flask-based file server for uploading/downloading files.
Core Components:
Component
Purpose
config.py
Centralized server configuration, authentication, storage settings
server.py
Flask application handling authentication, file operations, hotspot monitoring
controller.sh
Orchestrates hotspot activation, server start/stop, and system cleanup
Trial & Error Timeline (Annotated with Timestamps)
1. Initial Setup & Early Failures
Timestamp: 2025-10-07 16:10 – 16:50
Problem: Flask bound to 127.0.0.1; phones could not connect.
Errors: Race conditions from multiple startup methods; orphaned processes; false positive health checks.
Lesson Learned: Force 0.0.0.0 binding and use a single startup method.
2. Network Misconfiguration Trials
Timestamp: 2025-10-07 17:00 – 17:30
Problem: Intermittent connectivity; 34% packet loss, 1169ms latency.
Findings: Missing persistent NAT/MASQUERADE rules; client isolation still active.
Partial Success: Hotspot devices could ping server intermittently.
Lesson Learned: Always verify persistent NAT/firewall settings; disable client isolation.
3. Environment & Dependency Failures
Timestamp: 2025-10-08 09:15 – 09:45
Problem: ModuleNotFoundError: No module named 'flask'
Cause: Virtual environment not activated.
Lesson Learned: Always activate venv before server start (source venv/bin/activate).
4. First (Single) Fully Successful Run
Timestamp: 2025-10-09 18:57
Outcome: Hotspot (10.42.0.1) active; Flask server accessible from hotspot devices; NAT/MASQUERADE persistent; file operations functional.
Lesson Learned: Baseline confirmed working, but persistence across updates not guaranteed.
5. Post-Update Failures (After File Size Enhancements)
Timestamp: 2025-10-09 20:04 – 20:05
Problem: Server appeared to start; binding to * internally; external devices could not connect.
Lesson Learned: False positive health checks are misleading; updates may break persistent network configuration.
6. Additional Troubleshooting Trials
a) Reboot and Retest
Reset hotspot and NAT rules; server failed to bind externally.
Backup files confirmed working baseline.
b) Manual Network Verification
Checked iptables -t nat -L -n | grep MASQUERADE, IP forwarding (sysctl net.ipv4.ip_forward), and ap-isolation.
Lesson: Network rules must persist across reboots; controller should restore settings consistently.
c) Process Management Trials
pkill -f "python.*server.py" vs manual ps aux
Multiple orphaned processes caused intermittent failures.
Lesson: Single startup method; ensure cleanup before start.
Code Architecture & Logic
config.py – Configuration Hub
Authentication: PASSWORD (guest), ADMIN_PASSWORD (admin)
Server: MAX_USERS = 10, ALLOWED_EXTENSIONS, SERVER_PORT = 5000
Network: Trusted SSIDs
Storage: Public and private directories
Lesson: Centralized configuration reduces error, ensures security boundaries.
server.py – Flask File Server
Authentication: Token-based admin verification; dual-mode login
File Management: Upload, download, delete, admin delete
Hotspot Integration: Monitor status, enable/disable hotspot
Security: Allowed file types, secure filenames, session-based access, metadata logging
controller.sh – System Orchestration
Network Management: Clean interfaces, ensure hotspot profile, fault-tolerant activation
Server Control: Start/stop with health checks, single startup method
System Integration: DBUS environment, cleanup for graceful exit
Flow:
Copy code

Controller Start
    ↓
Ensure DBUS (Steam Deck)
    ↓
Create Hotspot (NetworkManager)
    ↓
Start Flask Server (0.0.0.0)
    ↓
Health Check Verification
    ↓
Phone Connection → Flask Authentication → File Operations
Lessons Learned (Do Not Repeat)
Always bind Flask to 0.0.0.0 for external access
Persistent NAT/MASQUERADE and IP forwarding are required
Disable hotspot client isolation
Single, consistent startup method only
Activate virtual environment before server start
Validate external connectivity, not just localhost
Incremental updates can break persistent network/server settings — backup first
Clear logging with timestamps is essential for troubleshooting
Recovery & Future Optimization Notes
Backup contains working baseline: can restore if updates break network
Health monitoring improvements: /deep_health route in server.py
Consider chunked uploads for large files, compression, caching, WebSockets for progress
Advanced: background processing (Celery), bandwidth monitoring (tc)
✅ Final Status:
Project now has complete documentation of trials, errors, partial successes, backups, and code structure.
Any future updates can reference this timeline to avoid repeated mistakes.
All network, server, and process lessons are timestamped for historical accuracy.
