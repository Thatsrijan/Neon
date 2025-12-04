# keepalive.py
import os
from threading import Thread
from flask import Flask

app = Flask("keepalive")

@app.route("/")
def home():
    return "OK — bot is alive", 200

def run_web():
    port = int(os.environ.get("PORT", 8080))  # Replit assigns PORT automatically
    app.run(host="0.0.0.0", port=port)

def start():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()
