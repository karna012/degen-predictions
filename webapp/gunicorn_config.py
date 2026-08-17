# Gunicorn configuration file for production
# Place this in the webapp directory and run: gunicorn -c gunicorn_config.py wsgi:app

import multiprocessing
import os

# Server socket
bind = "0.0.0.0:5000"
backlog = 2048

# Worker processes
workers = max(2, multiprocessing.cpu_count() - 1)
worker_class = "sync"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50
timeout = 120
keepalive = 2

# Logging
accesslog = os.path.join(os.path.dirname(__file__), "logs", "gunicorn_access.log")
errorlog = os.path.join(os.path.dirname(__file__), "logs", "gunicorn_error.log")
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "degenpredictions"

# Application
forwarded_allow_ips = "*"
secure_scheme_headers = {
    "X-PROTO": "https",
    "X-FORWARDED-PROTOCOL": "https",
    "X-FORWARDED-PROTO": "https",
    "X-FORWARDED-SSL": "on",
    "X-URL-SCHEME": "https",
}

# Server mechanics
preload_app = False
daemon = False
pidfile = None
tmp_upload_dir = None

# Environment
env = {"ENVIRONMENT": "production"}

# Post worker init
def post_worker_init(worker):
    pass

# Pre fork
def pre_fork(server, worker):
    pass

# Post fork  
def post_fork(server, worker):
    pass

# Worker abort
def worker_abort(worker):
    pass
