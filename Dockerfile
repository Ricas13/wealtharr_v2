FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/wealtharr
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x /srv/wealtharr/entrypoint.sh
EXPOSE 8000
CMD ["/srv/wealtharr/entrypoint.sh"]
