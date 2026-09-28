import os
import time
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from config import AGENT_NAME, PORT
from core.agent import AutonomousWorker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(AGENT_NAME)

worker = AutonomousWorker(AGENT_NAME)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        res = worker.perform_cycle()
        self.wfile.write(str(res).encode('utf-8'))
    def log_message(self, format, *args):
        pass

def start_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    logger.info(f'[{AGENT_NAME}] Слушаю порт {PORT}')
    server.serve_forever()

if __name__ == '__main__':
    logger.info(f'Запуск {AGENT_NAME}...')
    start_server()
