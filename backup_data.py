import os
import tarfile
from datetime import datetime, timedelta

# Define paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
DB_FILE = os.path.join(BASE_DIR, 'db.sqlite3')
MEDIA_DIR = os.path.join(BASE_DIR, 'media')

# Ensure backup directory exists
if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

# Generate backup filename based on current date and time
date_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
backup_filename = f"backup_{date_str}.tar.gz"
backup_filepath = os.path.join(BACKUP_DIR, backup_filename)

print(f"Starting backup: {backup_filename}...")

# Create tar.gz archive
try:
    with tarfile.open(backup_filepath, "w:gz") as tar:
        # Add database file to the archive
        if os.path.exists(DB_FILE):
            tar.add(DB_FILE, arcname='db.sqlite3')
            print(" - Added db.sqlite3 to archive.")
        else:
            print(" - Warning: db.sqlite3 not found!")

        # Add media folder to the archive
        if os.path.exists(MEDIA_DIR):
            tar.add(MEDIA_DIR, arcname='media')
            print(" - Added media/ folder to archive.")
        else:
            print(" - Info: media/ folder not found. Skipping.")
            
    print(f"Backup created successfully at: {backup_filepath}")
except Exception as e:
    print(f"Error creating backup: {e}")

# Clean up older backups (keep only the last 7 days)
print("Checking for old backups to clean up...")
retention_days = 7
now = datetime.now()

for filename in os.listdir(BACKUP_DIR):
    if filename.startswith("backup_") and filename.endswith(".tar.gz"):
        file_path = os.path.join(BACKUP_DIR, filename)
        # Get file modification time
        file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
        
        # If file is older than the retention period, delete it
        if now - file_mtime > timedelta(days=retention_days):
            try:
                os.remove(file_path)
                print(f" - Deleted old backup: {filename}")
            except Exception as e:
                print(f" - Failed to delete old backup {filename}: {e}")

print("Backup process finished.")
