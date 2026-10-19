FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/wealtharr
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system --gid 10001 wealtharr \
    && useradd --system --uid 10001 --gid wealtharr --home-dir /home/wealtharr --create-home wealtharr
COPY --chown=wealtharr:wealtharr . .
RUN mkdir -p /srv/wealtharr/staticfiles \
    && chown -R wealtharr:wealtharr /srv/wealtharr/staticfiles \
    && chmod +x /srv/wealtharr/entrypoint.sh
USER wealtharr
EXPOSE 8000
CMD ["/srv/wealtharr/entrypoint.sh"]
