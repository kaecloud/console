bind = '0.0.0.0:5000'
graceful_timeout = 3600
timeout = 1200
max_requests = 1200
workers = 1
worker_class = 'flask_sockets.worker'
