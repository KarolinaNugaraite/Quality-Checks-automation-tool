#!/usr/bin/env python
"""Run the web UI server."""
from metadata_checker.web import create_app

if __name__ == "__main__":
    app = create_app()
    print("\n" + "="*50)
    print("Metadata Checker Web UI")
    print("="*50)
    print("\n🌐 Open your browser: http://127.0.0.1:5000")
    print("\nPress CTRL+C to stop the server\n")
    app.run(debug=True, host="127.0.0.1", port=5000, threaded=True)
