# EC2 deployment beside the clocking application

Example layout:

```text
/home/ubuntu/
  restaurant_clocking/
  restaurant-roster/
```

Use a distinct virtual environment and service:

```bash
cd /home/ubuntu/restaurant-roster
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py collectstatic --noinput
```

Example Gunicorn command:

```bash
gunicorn config.wsgi:application --bind 127.0.0.1:8001
```

Create a separate `restaurant-roster.service`. Nginx can proxy `/roster/`
or a roster subdomain to port 8001. Restarting this service will not restart
the clocking service.
