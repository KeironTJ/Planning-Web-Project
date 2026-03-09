"""
WSGI entry point for production servers (Gunicorn, uWSGI).

Usage:
    gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run()
