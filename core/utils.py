from PIL import Image
from io import BytesIO
from django.core.files.base import ContentFile
import os

def compress_image(image_field, quality=70, max_width=1200):
    """
    Compresses an image before saving.
    """
    if not image_field:
        return

    # Check if the file is an image based on extension
    ext = os.path.splitext(image_field.name)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.webp']:
        return

    try:
        # Open the image using Pillow
        img = Image.open(image_field)
        
        # Convert to RGB if necessary (important for JPEG)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        
        # Resize if width exceeds max_width
        if img.width > max_width:
            output_size = (max_width, int((max_width / img.width) * img.height))
            img = img.resize(output_size, Image.Resampling.LANCZOS)
        
        # Save the image to a BytesIO object
        im_io = BytesIO()
        
        # Determine format for saving
        save_format = 'JPEG'
        if ext == '.png':
            # For PNG, we can still save as JPEG to reduce size, or keep PNG
            # If we want maximum compression, JPEG is better.
            # But maybe user wants to keep PNG transparency? 
            # The convert('RGB') above handles it by removing transparency.
            pass
        
        img.save(im_io, format=save_format, quality=quality, optimize=True)
        
        # Create a new ContentFile from the BytesIO object
        # Change extension to .jpg if we converted it
        new_name = os.path.splitext(image_field.name)[0] + '.jpg'
        
        image_field.save(new_name, ContentFile(im_io.getvalue()), save=False)
    except Exception as e:
        print(f"Error compressing image: {e}")
        # If compression fails, we just keep the original image
        pass

def log_activity(actor, action_type, description, related_obj=None, metadata=None, ip=None):
    """
    Helper to record a new ActivityLog entry.
    """
    from .models import ActivityLog
    
    log = ActivityLog(
        actor=actor,
        action_type=action_type,
        description=description,
        metadata=metadata or {},
        ip_address=ip
    )
    
    if related_obj:
        log.related_object_id = getattr(related_obj, 'id', None)
        log.related_object_type = related_obj.__class__.__name__
        
    log.save()
    return log
