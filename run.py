"""
run.py
======
Development server entry point for DishUp Menu Intelligence.

Usage:
    python run.py

Opens: http://localhost:5000
"""
import os
import sys

# Ensure project root is on the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.server.app import app

if __name__ == "__main__":
    print("DishUp Menu Intelligence - Development Server")
    print("URL : http://localhost:5000")
    print("API : http://localhost:5000/api/health")
    app.run(debug=False, host="127.0.0.1", port=5000, use_reloader=False)
