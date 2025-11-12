#!/usr/bin/env python3
"""
Waitress Wrapper for Unified File Server
OPTIMIZED: Fixed Waitress configuration for stable client upload speeds
SAFE: Zero breaking changes to original functionality
"""

import os
import sys
import signal
import atexit
from waitress import serve

# Import your existing working server
import unified_server

def graceful_shutdown():
    """Use the exact same shutdown logic from unified_server.py"""
    print("🛑 Waitress wrapper initiating graceful shutdown...")
    unified_server.graceful_shutdown()

def main():
    """OPTIMIZED Waitress wrapper with performance fixes"""
    # Get mode from environment (same as your Gunicorn approach)
    mode = os.environ.get('FILE_SERVER_MODE', 'lan')

    print(f"🚀 OPTIMIZED WAITRESS WRAPPER STARTING")
    print(f"   Mode: {mode.upper()}")
    print(f"   Using streaming-optimized unified_server.py")
    print(f"   PID: {os.getpid()}")
    print("=" * 50)

    # Setup graceful shutdown (same as original)
    signal.signal(signal.SIGINT, lambda s, f: graceful_shutdown())
    signal.signal(signal.SIGTERM, lambda s, f: graceful_shutdown())
    atexit.register(graceful_shutdown)

    try:
        # Start hotspot if needed using ORIGINAL code
        if mode == 'hotspot':
            print("🔥 Starting hotspot via original HotspotManager...")
            if not unified_server.hotspot_manager.start_hotspot_with_verification():
                print("❌ Hotspot setup failed, continuing in LAN mode")

        # Ensure directories (original function)
        unified_server.ensure_dirs()

        # 🚀 OPTIMIZED WAITRESS CONFIGURATION
        print(f"🌐 OPTIMIZED Waitress serving on 0.0.0.0:{unified_server.config.SERVER_PORT}")
        print("   Threads: 16 | Connection Limit: 500 | Buffer: 64KB")
        
        serve(
            unified_server.app,
            host='0.0.0.0', 
            port=unified_server.config.SERVER_PORT,
            # 🎯 CRITICAL PERFORMANCE SETTINGS FOR CLIENT UPLOADS
            threads=16,                    # More threads for concurrent transfers
            connection_limit=500,          # Higher connection limit to prevent queueing
            channel_timeout=300,           # 5-minute timeout for large file transfers
            asyncore_loop_timeout=1,       # Faster I/O polling for better responsiveness
            send_bytes=65536,              # 64KB buffer sizes for better throughput
            cleanup_interval=30,           # Less frequent cleanup to reduce overhead
            ident="OptimizedFileServer"    # Server identification
        )

    except KeyboardInterrupt:
        print("👋 Shutdown requested by user")
    except Exception as e:
        print(f"💥 Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
