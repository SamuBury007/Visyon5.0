FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app
COPY . .
RUN pip install flask playwright requests
RUN playwright install chromium

EXPOSE 8080
CMD ["python", "app.py"]
