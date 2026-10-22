# Separate encryption key: deliberate migration

A legacy install encrypts SMTP, webhook and TOTP secrets using a key derived
from Django SECRET_KEY. Rotating SECRET_KEY before re-encryption loses access to
those credentials. Nothing in this change rotates the existing Django key.

For an existing installation:
1. Create a verified PostgreSQL backup and separately escrow existing secrets.
2. Fetch the new code, then run bash wealtharr.sh enable-data-key (only once).
3. Deploy/rebuild: bash wealtharr.sh update. This automatically adds
   compose.data-key.yml when the new secret exists.
4. In the running web container, run python manage.py migrate_data_encryption.
   The transaction either re-encrypts ALL saved credentials or changes none.
5. Verify TOTP, email, Discord and Telegram. Back up again and securely export
   the new data_encryption_key OFF the host; retain it for any future restore.
6. Do not rotate Django SECRET_KEY until independent restore verification.

Fresh installations receive the separate key automatically at setup. Existing
installations may continue with their old key until they deliberately opt in.
