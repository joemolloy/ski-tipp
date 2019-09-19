from django.core.management.base import BaseCommand

class Command(BaseCommand):

    def handle(self, *args, **options):
        if not User.objects.filter(username="joemolloy").exists():
            User.objects.create_superuser("joemolloy", "joe.m34@gmail.com", "admin")