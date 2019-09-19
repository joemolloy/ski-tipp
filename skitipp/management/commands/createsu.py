from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

import os

class Command(BaseCommand):

    def handle(self, *args, **options):

        initial_password = os.environ.get('INITIAL_ISTV_SUPER_USER_PASSWORD', "password")

        if not User.objects.filter(username="joemolloy").exists():
            User.objects.create_superuser("joemolloy", "joe.m34@gmail.com", initial_password,"admin")