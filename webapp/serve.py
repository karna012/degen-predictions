# Production server runner for Windows using Waitress
# This script starts the Flask app with Waitress WSGI server

import os
import sys
from pathlib import Path

# Add webapp directory to path
webapp_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(webapp_dir))

from app import app

if __name__ == "__main__":
    print("=" * 70)
    print("Degen Predictions - Production Server (Waitress)")
    print("=" * 70)
    print()
    print("✓ Starting server on http://0.0.0.0:5000")
    print("✓ Workers: 4")
    print("✓ Timeout: 120 seconds")
    print()
    print("Press Ctrl+C to stop the server")
    print("-" * 70)
    print()
    
    try:
        # Import waitress
        from waitress import serve
        
        # Serve the app
        serve(
            app,
            host="0.0.0.0",
            port=5000,
            threads=4,
            _quiet=False,
            _start=True,
        )
    except KeyboardInterrupt:
        print("\n" + "-" * 70)
        print("Server stopped")
        print("=" * 70)
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error starting server: {e}")
        sys.exit(1)
