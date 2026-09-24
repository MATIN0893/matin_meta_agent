from services.patrol_service import patrol_loop
import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

load_dotenv()

TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN")

from bot.handlers.commands import start, repos, status
from bot.handlers.build_task import handle_message

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

def run_patrol(bot):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(patrol_loop(bot))

def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    request = HTTPXRequest(connect_timeout=60, read_timeout=60)
    app = ApplicationBuilder().token(TG_BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("repos", repos))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    threading.Thread(target=run_patrol, args=(app.bot,), daemon=True).start()

    print("🤖 Meta Agent запущен (SRE Patrol активен)")
    app.run_polling(timeout=30)

if __name__ == "__main__":
    main()

