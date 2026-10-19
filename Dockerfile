FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=wealtharr.settings
WORKDIR /srv/wealtharr
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --create-home wealtharr
COPY . .
RUN chmod 755 /srv/wealtharr/entrypoint.sh \
    && mkdir -p /srv/wealtharr/staticfiles \
    && chown -R wealtharr:wealtharr /srv/wealtharr
USER wealtharr
EXPOSE 8000
CMD ["/srv/wealtharr/entrypoint.sh"]
