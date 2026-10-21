import sys
import os

# Add files/ directory to python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "files"))

from web_app import app

if __name__ == "__main__":
    app.run()
