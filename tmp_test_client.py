import os
import django
import sys

# Set up Django
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from core.models import Client
from django.db.models import Count

def test_refresh_issue():
    # 1. Create a client
    name = "Script Test Client"
    client = Client.objects.create(name=name, status='Active')
    print(f"Created client: {client.id} - {client.name}")
    
    # 2. Query clients like the view does
    status = 'Active'
    clients = Client.objects.filter(status=status).annotate(active_project_count=Count('projects')).order_by('name')
    
    names = [c.name for c in clients]
    print(f"Clients in Active list: {names}")
    
    if name in names:
        print("SUCCESS: New client IS in the list.")
    else:
        print("FAILURE: New client IS NOT in the list.")
    
    # Cleanup
    client.delete()

if __name__ == "__main__":
    test_refresh_issue()
