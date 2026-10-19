from django.urls import path
from . import cockpit, cockpit_import, cockpit_settings

urlpatterns = [
    path('', cockpit.today, name='cockpit_today'),
    path('start/', cockpit.setup, name='cockpit_setup'),
    path('history/', cockpit.history, name='cockpit_history'),
    path('ledger/', cockpit.ledger, name='cockpit_ledger'),
    path('strategy/', cockpit.strategy_info, name='cockpit_strategy'),
    path('deposit/', cockpit.deposit_shortcut, name='cockpit_deposit'),
    path('prices/check/', cockpit.refresh_prices, name='cockpit_refresh'),
    path('rebalance/<int:signal_id>/', cockpit.guided_rebalance, name='cockpit_rebalance'),
    path('import/', cockpit_import.csv_import, name='cockpit_csv'),
    path('settings/', cockpit_settings.owner_settings, name='cockpit_settings'),
    path('settings/test/<str:channel>/', cockpit_settings.test_delivery, name='cockpit_test'),
]
