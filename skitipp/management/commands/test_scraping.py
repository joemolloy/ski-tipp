from django.core.management.base import BaseCommand
from skitipp.fis_connector import get_race_results

class Command(BaseCommand):
    def handle(self, **options):
        get_race_results(95527)
