FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app
COPY . .
RUN pip install flask playwright requests gunicorn
RUN playwright install chromium

EXPOSE 8080
# Singolo worker = stesso processo = stesso IP uscente per Playwright e proxy
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120"]
